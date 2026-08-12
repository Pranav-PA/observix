"""Streaming model responses.

Most production LLM calls stream, which breaks the assumption that a function's
return value is its output: by the time the decorated function returns, nothing
has been generated yet. Time-to-first-token --- often the number users actually
feel --- is invisible to a plain ``@observe``.

This module records a stream as it is consumed: TTFT on the first chunk, text
accumulated as they arrive, and the span finalised when the stream ends,
**including when it is abandoned part-way**. An unconsumed generator that gets
garbage-collected still produces a complete span rather than a dangling one.

    from observix import observe, observe_stream

    @observe(kind="chat")
    def chat(prompt: str):
        stream = client.messages.create(..., stream=True)
        return observe_stream(
            stream,
            extract=lambda chunk: chunk.delta.text,
            provider="anthropic",
            request_model="claude-opus-4",
        )
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Iterator
from typing import Any, TypeVar

from .errors import suppress_and_log
from .model.enums import SpanKind
from .model.span import ObservixSpan

T = TypeVar("T")

#: Chunk shapes seen across vendor SDKs, tried in order by the default
#: extractor. Covers OpenAI deltas, Anthropic events, and plain strings.
_TEXT_PATHS: tuple[tuple[str | int, ...], ...] = (
    ("delta", "text"),
    ("delta", "content"),
    ("choices", 0, "delta", "content"),
    ("text",),
    ("content",),
)


def default_extract(chunk: Any) -> str | None:
    """Best-effort text extraction from a vendor chunk. Never raises."""
    if chunk is None:
        return None
    if isinstance(chunk, str):
        return chunk

    for path in _TEXT_PATHS:
        value: Any = chunk
        for step in path:
            if value is None:
                break
            if isinstance(step, int):
                value = value[step] if isinstance(value, (list, tuple)) and value else None
            elif isinstance(value, dict):
                value = value.get(step)
            else:
                value = getattr(value, step, None)
        if isinstance(value, str) and value:
            return value
    return None


class StreamRecorder:
    """Accumulates a streaming response onto a span.

    Timing starts when the recorder is constructed, so create it immediately
    before consuming the stream for TTFT to mean anything.
    """

    __slots__ = ("_count", "_extract", "_finished", "_parts", "_span", "_start", "_ttft_ms")

    def __init__(
        self,
        span: ObservixSpan,
        *,
        extract: Callable[[Any], str | None] | None = None,
    ) -> None:
        self._span = span
        self._extract = extract or default_extract
        self._start = time.perf_counter()
        self._ttft_ms: float | None = None
        self._parts: list[str] = []
        self._count = 0
        self._finished = False

    @property
    def span(self) -> ObservixSpan:
        return self._span

    @property
    def text(self) -> str:
        """Everything accumulated so far."""
        return "".join(self._parts)

    @property
    def chunk_count(self) -> int:
        return self._count

    @property
    def time_to_first_token_ms(self) -> float | None:
        """Milliseconds from recorder creation to the first non-empty chunk."""
        return self._ttft_ms

    def add_chunk(self, chunk: Any) -> None:
        """Record one chunk. Safe to call after finishing (ignored)."""
        if self._finished:
            return
        self._count += 1
        with suppress_and_log("StreamRecorder.add_chunk"):
            text = self._extract(chunk)
            if text:
                if self._ttft_ms is None:
                    self._ttft_ms = (time.perf_counter() - self._start) * 1000.0
                self._parts.append(text)

    def finish(self, **llm_kwargs: Any) -> None:
        """Write the accumulated response onto the span. Idempotent.

        ``llm_kwargs`` are forwarded to
        :meth:`~observix.model.span.ObservixSpan.record_llm_call`, so model,
        provider and token usage can be supplied once the stream has revealed
        them.
        """
        if self._finished:
            return
        self._finished = True

        with suppress_and_log("StreamRecorder.finish"):
            text = self.text
            if llm_kwargs:
                llm_kwargs.setdefault("kind", SpanKind.CHAT)
                if text and "output_messages" not in llm_kwargs:
                    llm_kwargs["output_messages"] = [{"role": "assistant", "content": text}]
                self._span.record_llm_call(streaming=True, **llm_kwargs)
            else:
                if text:
                    self._span.set_output_messages([{"role": "assistant", "content": text}])
                self._span.set_response_metadata(streaming=True)

            self._span.set_response_metadata(time_to_first_token_ms=self._ttft_ms, streaming=True)
            self._span.set_metadata(stream_chunks=self._count)

    def __repr__(self) -> str:
        return (
            f"<StreamRecorder chunks={self._count} "
            f"ttft_ms={self._ttft_ms} finished={self._finished}>"
        )


def _resolve_target(
    span: ObservixSpan | None,
    name: str | None,
    kind: SpanKind | str,
) -> tuple[ObservixSpan, bool]:
    """Pick the span to record onto, resolved **eagerly** at call time.

    Returns ``(span, we_own_it)``.

    Eager resolution matters: the wrappers below are generators, so their
    bodies do not run until the first chunk is pulled. By then the enclosing
    ``@observe`` function has usually returned and its span has ended, and
    writes to an ended span go nowhere.

    Defaulting to a *new* span rather than the current one is what makes

        return observe_stream(...)

    behave correctly --- the stream gets a span that lives exactly as long as
    the streaming does, parented to whatever was current when the call was
    made. Pass ``span=`` explicitly to record onto a span you already own, for
    instance from ``yield from`` inside a decorated generator.
    """
    if span is not None:
        return span, False

    from .api import start_span

    return start_span(name or "stream", kind=kind), True


def observe_stream(
    stream: Iterable[T],
    *,
    span: ObservixSpan | None = None,
    name: str | None = None,
    kind: SpanKind | str = SpanKind.CHAT,
    extract: Callable[[Any], str | None] | None = None,
    **llm_kwargs: Any,
) -> Iterator[T]:
    """Wrap a sync stream, recording TTFT and the accumulated response.

    Chunks pass through untouched.

    Args:
        stream: The vendor's chunk iterator.
        span: Record onto this span instead of creating one. The caller keeps
            responsibility for ending it.
        name: Name for the span created when ``span`` is not given.
        kind: Canonical kind for that span.
        extract: Pull display text out of a chunk. Defaults to a heuristic
            covering OpenAI, Anthropic and plain-string chunks.
        **llm_kwargs: Forwarded to ``record_llm_call`` on completion.

    Yields:
        Each chunk, unmodified.
    """
    # Deliberately NOT a generator function: the span must be resolved now,
    # while the caller's context is still active, not at first `next()`.
    target, owned = _resolve_target(span, name, kind)
    recorder = StreamRecorder(target, extract=extract)
    return _drive(stream, target, recorder, owned, llm_kwargs)


def _drive(
    stream: Iterable[T],
    target: ObservixSpan,
    recorder: StreamRecorder,
    owned: bool,
    llm_kwargs: dict[str, Any],
) -> Iterator[T]:
    try:
        for chunk in stream:
            recorder.add_chunk(chunk)
            yield chunk
    except BaseException as exc:
        # Covers GeneratorExit too, so an abandoned stream still finalises.
        # Abandonment is not an error, so it is not recorded as one.
        if not isinstance(exc, GeneratorExit):
            with suppress_and_log("observe_stream.record_exception"):
                target.record_exception(exc)
        raise
    finally:
        recorder.finish(**llm_kwargs)
        if owned:
            target.end()


def observe_astream(
    stream: AsyncIterable[T],
    *,
    span: ObservixSpan | None = None,
    name: str | None = None,
    kind: SpanKind | str = SpanKind.CHAT,
    extract: Callable[[Any], str | None] | None = None,
    **llm_kwargs: Any,
) -> AsyncIterator[T]:
    """Async counterpart of :func:`observe_stream`.

    Not itself a coroutine --- it returns an async iterator, so the span is
    resolved when you call it rather than at the first ``await``.
    """
    target, owned = _resolve_target(span, name, kind)
    recorder = StreamRecorder(target, extract=extract)
    return _adrive(stream, target, recorder, owned, llm_kwargs)


async def _adrive(
    stream: AsyncIterable[T],
    target: ObservixSpan,
    recorder: StreamRecorder,
    owned: bool,
    llm_kwargs: dict[str, Any],
) -> AsyncIterator[T]:
    try:
        async for chunk in stream:
            recorder.add_chunk(chunk)
            yield chunk
    except BaseException as exc:
        if not isinstance(exc, GeneratorExit):
            with suppress_and_log("observe_astream.record_exception"):
                target.record_exception(exc)
        raise
    finally:
        recorder.finish(**llm_kwargs)
        if owned:
            target.end()
