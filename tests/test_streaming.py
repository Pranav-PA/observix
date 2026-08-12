"""Streaming model responses."""

from __future__ import annotations

import asyncio

import pytest

from observix import get_current_span, observe, observe_astream, observe_stream
from observix.model.enums import SpanKind
from observix.semconv import canonical as C
from observix.streaming import StreamRecorder, default_extract
from observix.testing import collect_spans


class TestDefaultExtract:
    def test_plain_strings(self) -> None:
        assert default_extract("hello") == "hello"

    def test_openai_style_delta(self) -> None:
        chunk = {"choices": [{"delta": {"content": "hi"}}]}
        assert default_extract(chunk) == "hi"

    def test_anthropic_style_delta(self) -> None:
        assert default_extract({"delta": {"text": "hi"}}) == "hi"

    def test_object_attributes_not_just_dicts(self) -> None:
        class Delta:
            text = "hi"

        class Chunk:
            delta = Delta()

        assert default_extract(Chunk()) == "hi"

    def test_unrecognised_chunks_yield_none(self) -> None:
        assert default_extract({"unknown": "shape"}) is None
        assert default_extract(None) is None

    def test_empty_choices_do_not_raise(self) -> None:
        assert default_extract({"choices": []}) is None


class TestOwnedSpan:
    """By default a stream owns a span covering the streaming itself.

    Regression guard: ``observe_stream`` used to record onto the *current*
    span, resolved lazily inside a generator body. With ``return
    observe_stream(...)`` --- the most natural usage --- the enclosing
    ``@observe`` had already returned and ended its span before the first
    chunk arrived, so every write silently went nowhere.
    """

    def test_returning_a_stream_still_records(self) -> None:
        @observe(kind="workflow", name="outer")
        def outer():
            return observe_stream(iter(["Hello", " world"]), name="stream")

        with collect_spans() as spans:
            assert list(outer()) == ["Hello", " world"]

        stream = spans.first_named("stream")
        assert "Hello world" in stream.attributes[C.OUTPUT_MESSAGES]

    def test_the_stream_span_is_a_child_of_the_caller(self) -> None:
        @observe(kind="workflow", name="outer")
        def outer():
            return observe_stream(iter(["a"]), name="stream")

        with collect_spans() as spans:
            list(outer())

        outer_span = spans.first_named("outer")
        stream = spans.first_named("stream")
        assert stream.parent is not None
        assert stream.parent.span_id == outer_span.get_span_context().span_id

    def test_the_owned_span_is_ended(self) -> None:
        with collect_spans() as spans:
            list(observe_stream(iter(["a"]), name="stream"))
        assert spans.first_named("stream").end_time is not None

    def test_default_kind_is_chat(self) -> None:
        with collect_spans() as spans:
            list(observe_stream(iter(["a"]), name="stream"))
        assert spans.first_named("stream").attributes[C.KIND] == SpanKind.CHAT.value

    def test_kind_can_be_overridden(self) -> None:
        with collect_spans() as spans:
            list(observe_stream(iter(["a"]), name="stream", kind="agent"))
        assert spans.first_named("stream").attributes[C.KIND] == SpanKind.AGENT.value


class TestExplicitSpan:
    """Passing span= records onto a span the caller already owns."""

    def test_records_onto_the_given_span(self) -> None:
        @observe(kind="chat", name="chat")
        def chat():
            return list(observe_stream(iter(["Hello", " world"]), span=get_current_span()))

        with collect_spans() as spans:
            chat()

        assert len(spans) == 1  # no child span created
        assert "Hello world" in spans.one().attributes[C.OUTPUT_MESSAGES]

    def test_the_given_span_is_not_ended_by_the_stream(self) -> None:
        @observe(kind="chat", name="chat")
        def chat():
            stream = observe_stream(iter(["a"]), span=get_current_span())
            list(stream)
            # Still inside the function: the span must still be recording.
            assert get_current_span().is_recording is True

        with collect_spans():
            chat()

    def test_yield_from_inside_a_decorated_generator(self) -> None:
        """The other correct pattern: the wrapper's span covers the iteration."""

        @observe(kind="chat", name="chat")
        def chat():
            yield from observe_stream(iter(["a", "b"]), span=get_current_span())

        with collect_spans() as spans:
            assert list(chat()) == ["a", "b"]

        assert len(spans) == 1
        assert "ab" in spans.one().attributes[C.OUTPUT_MESSAGES]


class TestSyncStreaming:
    def test_chunks_pass_through_untouched(self) -> None:
        with collect_spans():
            assert list(observe_stream(iter(["a", "b", "c"]))) == ["a", "b", "c"]

    def test_records_time_to_first_token(self) -> None:
        with collect_spans() as spans:
            list(observe_stream(iter(["a", "b"]), name="stream"))

        attrs = spans.first_named("stream").attributes
        assert attrs[C.LLM_TIME_TO_FIRST_TOKEN_MS] >= 0
        assert attrs[C.LLM_STREAMING] is True

    def test_records_chunk_count(self) -> None:
        with collect_spans() as spans:
            list(observe_stream(iter(["a", "b", "c"]), name="stream"))

        attrs = spans.first_named("stream").attributes
        assert attrs[C.metadata_key("stream_chunks")] == 3

    def test_llm_metadata_is_forwarded(self) -> None:
        with collect_spans() as spans:
            list(
                observe_stream(
                    iter(["hi"]),
                    name="stream",
                    provider="anthropic",
                    request_model="claude-opus-4",
                    input_tokens=10,
                    output_tokens=1,
                )
            )

        attrs = spans.first_named("stream").attributes
        assert attrs[C.LLM_PROVIDER] == "anthropic"
        assert attrs[C.LLM_REQUEST_MODEL] == "claude-opus-4"
        assert attrs[C.USAGE_INPUT_TOKENS] == 10

    def test_cost_is_computed_for_a_stream(self) -> None:
        with collect_spans() as spans:
            list(
                observe_stream(
                    iter(["hi"]),
                    name="stream",
                    provider="anthropic",
                    request_model="claude-opus-4",
                    input_tokens=1_000_000,
                    output_tokens=0,
                )
            )

        assert spans.first_named("stream").attributes[C.COST_TOTAL_USD] > 0

    def test_an_abandoned_stream_still_finalises(self) -> None:
        """A generator dropped part-way must not leave a half-recorded span."""
        with collect_spans() as spans:
            stream = observe_stream(iter(["a", "b", "c", "d"]), name="stream")
            assert next(stream) == "a"
            stream.close()  # abandon the rest

        attrs = spans.first_named("stream").attributes
        assert "a" in attrs[C.OUTPUT_MESSAGES]
        assert attrs[C.LLM_STREAMING] is True
        assert C.ERROR_TYPE not in attrs  # abandoning is not an error

    def test_an_error_mid_stream_is_recorded(self) -> None:
        def failing():
            yield "partial"
            raise RuntimeError("stream died")

        with collect_spans() as spans, pytest.raises(RuntimeError, match="stream died"):
            list(observe_stream(failing(), name="stream"))

        attrs = spans.first_named("stream").attributes
        assert attrs[C.ERROR_TYPE] == "RuntimeError"
        assert "partial" in attrs[C.OUTPUT_MESSAGES]

    def test_an_empty_stream_does_not_raise(self) -> None:
        with collect_spans() as spans:
            assert list(observe_stream(iter([]), name="stream")) == []
        assert spans.first_named("stream").attributes[C.LLM_STREAMING] is True

    def test_works_when_unconfigured(self) -> None:
        assert list(observe_stream(iter(["a", "b"]))) == ["a", "b"]


class TestAsyncStreaming:
    async def test_chunks_pass_through(self) -> None:
        async def gen():
            for chunk in ("a", "b"):
                await asyncio.sleep(0)
                yield chunk

        with collect_spans():
            assert [c async for c in observe_astream(gen())] == ["a", "b"]

    async def test_accumulates_and_times(self) -> None:
        async def gen():
            for chunk in ("Hello", " world"):
                await asyncio.sleep(0)
                yield chunk

        with collect_spans() as spans:
            [c async for c in observe_astream(gen(), name="stream", provider="openai")]

        attrs = spans.first_named("stream").attributes
        assert "Hello world" in attrs[C.OUTPUT_MESSAGES]
        assert attrs[C.LLM_TIME_TO_FIRST_TOKEN_MS] >= 0
        assert attrs[C.LLM_PROVIDER] == "openai"

    async def test_returning_an_async_stream_still_records(self) -> None:
        """The async half of the same regression."""

        async def gen():
            for chunk in ("a", "b"):
                await asyncio.sleep(0)
                yield chunk

        @observe(kind="workflow", name="outer")
        async def outer():
            return observe_astream(gen(), name="stream")

        with collect_spans() as spans:
            stream = await outer()
            assert [c async for c in stream] == ["a", "b"]

        assert "ab" in spans.first_named("stream").attributes[C.OUTPUT_MESSAGES]

    async def test_an_error_mid_stream_is_recorded(self) -> None:
        async def gen():
            yield "partial"
            raise RuntimeError("async stream died")

        with collect_spans() as spans, pytest.raises(RuntimeError):
            [c async for c in observe_astream(gen(), name="stream")]

        assert spans.first_named("stream").attributes[C.ERROR_TYPE] == "RuntimeError"

    async def test_an_abandoned_async_stream_finalises(self) -> None:
        async def gen():
            for chunk in ("a", "b", "c"):
                await asyncio.sleep(0)
                yield chunk

        with collect_spans() as spans:
            stream = observe_astream(gen(), name="stream")
            assert await stream.__anext__() == "a"
            await stream.aclose()

        assert "a" in spans.first_named("stream").attributes[C.OUTPUT_MESSAGES]


class TestStreamRecorder:
    def test_exposes_accumulated_state(self) -> None:
        with collect_spans():
            recorder = StreamRecorder(get_current_span())
            recorder.add_chunk("a")
            recorder.add_chunk("b")
            assert recorder.text == "ab"
            assert recorder.chunk_count == 2
            assert recorder.time_to_first_token_ms is not None

    def test_finish_is_idempotent(self) -> None:
        @observe(kind="chat", name="chat")
        def chat():
            recorder = StreamRecorder(get_current_span())
            recorder.add_chunk("x")
            recorder.finish()
            recorder.finish()  # must not raise or double-write

        with collect_spans() as spans:
            chat()

        assert spans.one().attributes[C.metadata_key("stream_chunks")] == 1

    def test_chunks_after_finish_are_ignored(self) -> None:
        with collect_spans():
            recorder = StreamRecorder(get_current_span())
            recorder.add_chunk("a")
            recorder.finish()
            recorder.add_chunk("b")
            assert recorder.text == "a"

    def test_a_failing_extractor_does_not_break_the_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(chunk):
            raise ValueError("bad extractor")

        # The suite runs with OBSERVIX_STRICT=1, which deliberately disables the
        # fail-open guard. Turn it off to exercise production behaviour.
        monkeypatch.delenv("OBSERVIX_STRICT", raising=False)

        with collect_spans():
            assert list(observe_stream(iter(["a", "b"]), extract=explode)) == ["a", "b"]

    def test_ttft_ignores_empty_leading_chunks(self) -> None:
        """Vendors often send role/metadata frames before any text."""
        with collect_spans():
            recorder = StreamRecorder(get_current_span())
            recorder.add_chunk({"unknown": "shape"})
            assert recorder.time_to_first_token_ms is None
            recorder.add_chunk("real text")
            assert recorder.time_to_first_token_ms is not None
