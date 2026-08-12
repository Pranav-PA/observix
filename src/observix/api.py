"""The provider-agnostic developer API.

Four entry points, in increasing order of control:

* :func:`observe` --- decorate a function, sync or async
* :func:`observe_block` --- instrument a block of code
* :func:`start_span` --- start a span you end yourself
* :func:`get_current_span` --- annotate whatever span is already active

None of them name a backend. Which backends receive the resulting telemetry is
decided entirely by :func:`observix.configure`.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import (
    Any,
    TypeVar,
    overload,
)

from opentelemetry import context as context_api
from opentelemetry import trace as trace_api
from opentelemetry.trace import SpanKind as OTelSpanKind
from typing_extensions import ParamSpec

from ._serde import to_json
from .errors import logger, strict_mode, suppress_and_log
from .model.enums import SpanKind
from .model.span import NoOpSpan, ObservixSpan
from .state import runtime

P = ParamSpec("P")
R = TypeVar("R")

#: Reused for every call while observix is disabled, so the decorator allocates
#: nothing on the hot path.
_NOOP_SPAN = NoOpSpan()

#: Argument names never recorded, regardless of ``capture_input``.
DEFAULT_EXCLUDED_ARGS = frozenset({"self", "cls"})


def _current_tracer() -> trace_api.Tracer | None:
    state = runtime()
    return state.tracer if state.enabled else None


# --- observe -----------------------------------------------------------------


@overload
def observe(func: Callable[P, R]) -> Callable[P, R]: ...


@overload
def observe(
    *,
    name: str | None = ...,
    kind: SpanKind | str = ...,
    capture_input: bool = ...,
    capture_output: bool = ...,
    exclude_args: Sequence[str] | None = ...,
    attributes: Mapping[str, Any] | None = ...,
    metadata: Mapping[str, Any] | None = ...,
    record_exceptions: bool = ...,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def observe(
    func: Callable[P, R] | None = None,
    *,
    name: str | None = None,
    kind: SpanKind | str = SpanKind.TASK,
    capture_input: bool = True,
    capture_output: bool = True,
    exclude_args: Sequence[str] | None = None,
    attributes: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    record_exceptions: bool = True,
) -> Any:
    """Trace a function. Works bare or with arguments.

    Handles sync functions, coroutines, generators and async generators; for
    generators the span covers the whole iteration, not just the call that
    creates them.

    Examples:
        >>> @observe
        ... def handle(request): ...

        >>> @observe(kind="agent", name="planner")
        ... async def plan(goal: str): ...

    Args:
        name: Span name. Defaults to the function's qualified name.
        kind: Canonical :class:`~observix.model.enums.SpanKind`.
        capture_input: Record the call's arguments.
        capture_output: Record the return value.
        exclude_args: Argument names to omit from captured input. ``self`` and
            ``cls`` are always omitted.
        attributes: Raw attributes set on every span from this function.
        metadata: Values recorded under ``observix.metadata.*``.
        record_exceptions: Record raised exceptions and set an error status.

    Returns:
        The wrapped function, with signature and metadata preserved.
    """

    def decorate(target: Callable[P, R]) -> Callable[P, R]:
        span_name = name or _default_name(target)
        span_kind = SpanKind.coerce(kind)
        excluded = DEFAULT_EXCLUDED_ARGS | set(exclude_args or ())
        options = _SpanOptions(
            name=span_name,
            kind=span_kind,
            capture_input=capture_input,
            capture_output=capture_output,
            excluded_args=excluded,
            attributes=dict(attributes) if attributes else None,
            metadata=dict(metadata) if metadata else None,
            record_exceptions=record_exceptions,
            signature=_safe_signature(target),
        )

        if inspect.isasyncgenfunction(target):
            return _wrap_async_generator(target, options)
        if inspect.iscoroutinefunction(target):
            return _wrap_coroutine(target, options)
        if inspect.isgeneratorfunction(target):
            return _wrap_generator(target, options)
        return _wrap_sync(target, options)

    if func is not None:
        return decorate(func)
    return decorate


class _SpanOptions:
    """Everything about a decorated function, resolved once at decoration."""

    __slots__ = (
        "attributes",
        "capture_input",
        "capture_output",
        "excluded_args",
        "has_var_positional",
        "kind",
        "metadata",
        "name",
        "positional_names",
        "record_exceptions",
        "signature",
    )

    def __init__(
        self,
        *,
        name: str,
        kind: SpanKind,
        capture_input: bool,
        capture_output: bool,
        excluded_args: frozenset[str] | set[str],
        attributes: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
        record_exceptions: bool,
        signature: inspect.Signature | None,
    ) -> None:
        self.name = name
        self.kind = kind
        self.capture_input = capture_input
        self.capture_output = capture_output
        self.excluded_args = excluded_args
        self.attributes = attributes
        self.metadata = metadata
        self.record_exceptions = record_exceptions
        self.signature = signature
        # Positional parameter names, resolved once here so the hot path can
        # zip them against args instead of calling Signature.bind_partial ---
        # measured at ~35us per call, by far the largest single cost in
        # argument capture.
        self.positional_names, self.has_var_positional = _positional_names(signature)


def _default_name(target: Callable[..., Any]) -> str:
    name = getattr(target, "__qualname__", None) or getattr(target, "__name__", None)
    return str(name) if name else "anonymous"


def _safe_signature(target: Callable[..., Any]) -> inspect.Signature | None:
    try:
        return inspect.signature(target)
    except (TypeError, ValueError):  # builtins, some C extensions
        return None


def _positional_names(
    signature: inspect.Signature | None,
) -> tuple[tuple[str, ...], bool]:
    """Names of positionally-passable parameters, and whether ``*args`` exists."""
    if signature is None:
        return (), False
    names: list[str] = []
    has_var_positional = False
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            names.append(parameter.name)
        elif parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            has_var_positional = True
    return tuple(names), has_var_positional


def _begin(
    tracer: trace_api.Tracer, options: _SpanOptions, args: Any, kwargs: Any
) -> tuple[ObservixSpan, object]:
    """Start a span, make it current, and record the call's inputs.

    Returns the facade rather than the raw span so ``_finish`` can reuse it
    instead of allocating a second one and re-reading the runtime.
    """
    otel_span = tracer.start_span(options.name, kind=OTelSpanKind.INTERNAL)
    token = context_api.attach(trace_api.set_span_in_context(otel_span))

    state = runtime()
    record_content = state.record_content
    span = ObservixSpan(otel_span, record_content=record_content)
    span.set_kind(options.kind)

    if options.attributes:
        span.set_attributes(options.attributes)
    if options.metadata:
        span.set_metadata(options.metadata)
    if options.capture_input and record_content:
        with suppress_and_log("observe.capture_input"):
            captured = _bind_arguments(options, args, kwargs)
            if captured:
                span.set_input(captured)

    return span, token


def _finish(
    span: ObservixSpan,
    token: Any,
    options: _SpanOptions,
    result: Any = None,
    exc: BaseException | None = None,
) -> None:
    """Record the outcome, detach the context, and end the span."""
    try:
        if exc is not None:
            if options.record_exceptions:
                span.record_exception(exc)
        else:
            if options.capture_output and result is not None:
                span.set_output(result)  # no-ops when content is not retained
    finally:
        with suppress_and_log("observe.detach"):
            context_api.detach(token)
        with suppress_and_log("observe.end"):
            span.end()


def _bind_arguments(options: _SpanOptions, args: Any, kwargs: Any) -> dict[str, Any] | None:
    """Render a call's arguments as a name-keyed mapping.

    Uses parameter names resolved at decoration time rather than
    ``Signature.bind_partial``, which is the single most expensive step in
    argument capture. Falls back to positional keys when the signature could
    not be introspected, and to full binding only for ``*args`` functions,
    where positional zipping cannot name every argument.
    """
    excluded = options.excluded_args

    if options.signature is None:
        captured: dict[str, Any] = {f"arg{i}": a for i, a in enumerate(args)}
        captured.update({k: v for k, v in kwargs.items() if k not in excluded})
        return captured or None

    if not options.has_var_positional:
        names = options.positional_names
        bound: dict[str, Any] = {}
        # strict=False is deliberate: a caller relying on defaults passes fewer
        # positional args than the function declares.
        for name, value in zip(names, args, strict=False):
            if name not in excluded:
                bound[name] = value
        # Anything beyond the declared positionals is a caller error that
        # Python itself will raise on; recording it would be noise.
        for key, value in kwargs.items():
            if key not in excluded:
                bound[key] = value
        return bound or None

    # *args functions only: positional zipping cannot name the variadic tail,
    # so fall back to full binding despite its cost.
    try:
        bound_arguments = options.signature.bind_partial(*args, **kwargs)
    except TypeError:
        return None
    return {
        key: value for key, value in bound_arguments.arguments.items() if key not in excluded
    } or None


def _wrap_sync(target: Callable[P, R], options: _SpanOptions) -> Callable[P, R]:
    @functools.wraps(target)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        tracer = _current_tracer()
        if tracer is None:
            return target(*args, **kwargs)

        span, token = _begin(tracer, options, args, kwargs)
        try:
            result = target(*args, **kwargs)
        except BaseException as exc:
            _finish(span, token, options, exc=exc)
            raise
        _finish(span, token, options, result=result)
        return result

    return wrapper


def _wrap_coroutine(target: Callable[P, Any], options: _SpanOptions) -> Callable[P, Any]:
    @functools.wraps(target)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
        tracer = _current_tracer()
        if tracer is None:
            return await target(*args, **kwargs)

        span, token = _begin(tracer, options, args, kwargs)
        try:
            result = await target(*args, **kwargs)
        except BaseException as exc:
            _finish(span, token, options, exc=exc)
            raise
        _finish(span, token, options, result=result)
        return result

    return wrapper


def _wrap_generator(target: Callable[P, Any], options: _SpanOptions) -> Callable[P, Any]:
    @functools.wraps(target)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Iterator[Any]:
        tracer = _current_tracer()
        if tracer is None:
            yield from target(*args, **kwargs)
            return

        span, token = _begin(tracer, options, args, kwargs)
        items: list = []
        try:
            for item in target(*args, **kwargs):
                if options.capture_output:
                    items.append(item)
                yield item
        except BaseException as exc:
            _finish(span, token, options, exc=exc)
            raise
        _finish(span, token, options, result=items if items else None)

    return wrapper


def _wrap_async_generator(target: Callable[P, Any], options: _SpanOptions) -> Callable[P, Any]:
    @functools.wraps(target)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> AsyncIterator[Any]:
        tracer = _current_tracer()
        if tracer is None:
            async for item in target(*args, **kwargs):
                yield item
            return

        span, token = _begin(tracer, options, args, kwargs)
        items: list = []
        try:
            async for item in target(*args, **kwargs):
                if options.capture_output:
                    items.append(item)
                yield item
        except BaseException as exc:
            _finish(span, token, options, exc=exc)
            raise
        _finish(span, token, options, result=items if items else None)

    return wrapper


# --- observe_block -----------------------------------------------------------


@contextmanager
def observe_block(
    name: str,
    *,
    kind: SpanKind | str = SpanKind.TASK,
    attributes: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    record_exceptions: bool = True,
) -> Iterator[ObservixSpan]:
    """Instrument a block of code.

    The span is made current for the duration, so anything called inside
    becomes a child --- including across ``await``, since context propagation
    rides on contextvars.

    Example:
        >>> with observe_block("retrieval", kind="retriever") as span:
        ...     docs = search(query)
        ...     span.set_retrieval(query=query, documents=docs)
    """
    tracer = _current_tracer()
    if tracer is None:
        yield _NOOP_SPAN
        return

    otel_span = tracer.start_span(name, kind=OTelSpanKind.INTERNAL)
    token = context_api.attach(trace_api.set_span_in_context(otel_span))

    state = runtime()
    span = ObservixSpan(otel_span, record_content=state.record_content)
    span.set_kind(kind)
    if attributes:
        span.set_attributes(attributes)
    if metadata:
        span.set_metadata(metadata)

    try:
        yield span
    except BaseException as exc:
        if record_exceptions:
            span.record_exception(exc)
        raise
    finally:
        with suppress_and_log("observe_block.detach"):
            context_api.detach(token)
        with suppress_and_log("observe_block.end"):
            otel_span.end()


# --- Manual spans ------------------------------------------------------------


def start_span(
    name: str,
    *,
    kind: SpanKind | str = SpanKind.TASK,
    attributes: Mapping[str, Any] | None = None,
    make_current: bool = False,
) -> ObservixSpan:
    """Start a span that you end yourself with :meth:`ObservixSpan.end`.

    Prefer :func:`observe_block` where the lifetime is lexical. This exists for
    spans whose start and end are genuinely far apart --- a request that
    completes in a callback, say.

    Args:
        make_current: Also install the span as the current context. When
            ``True`` you are responsible for detaching it; most callers should
            use :func:`observe_block` instead.
    """
    tracer = _current_tracer()
    if tracer is None:
        return _NOOP_SPAN

    otel_span = tracer.start_span(name, kind=OTelSpanKind.INTERNAL)
    if make_current:
        context_api.attach(trace_api.set_span_in_context(otel_span))

    span = ObservixSpan(otel_span, record_content=runtime().record_content)
    span.set_kind(kind)
    if attributes:
        span.set_attributes(attributes)
    return span


def get_current_span() -> ObservixSpan:
    """The span currently in context.

    Always returns a usable object --- a no-op span when nothing is active ---
    so callers never need a ``None`` check.
    """
    state = runtime()
    if not state.enabled:
        return _NOOP_SPAN
    otel_span = trace_api.get_current_span()
    if otel_span is trace_api.INVALID_SPAN:
        return _NOOP_SPAN
    return ObservixSpan(otel_span, record_content=state.record_content)


# --- Context propagation -----------------------------------------------------


def inject_context(carrier: dict[str, str] | None = None) -> dict[str, str]:
    """Serialise the active trace context into a carrier for an outbound call.

    Uses the configured global propagator (W3C ``traceparent`` by default), so
    the remote service continues the same trace.
    """
    from opentelemetry.propagate import inject

    target = carrier if carrier is not None else {}
    with suppress_and_log("inject_context"):
        inject(target)
    return target


def extract_context(carrier: Mapping[str, str]) -> Any:
    """Rebuild a trace context from an inbound carrier.

    Pass the result to :func:`attach_context` to continue the remote trace.
    """
    from opentelemetry.propagate import extract

    try:
        return extract(dict(carrier))
    except Exception:
        if strict_mode():
            raise
        logger.warning("observix: failed to extract trace context", exc_info=True)
        return None


@contextmanager
def attach_context(context: Any) -> Iterator[None]:
    """Activate a context produced by :func:`extract_context`."""
    if context is None:
        yield
        return
    token = context_api.attach(context)
    try:
        yield
    finally:
        with suppress_and_log("attach_context.detach"):
            context_api.detach(token)


def current_trace_id() -> str | None:
    """The active trace id as 32 lowercase hex characters, if any."""
    return get_current_span().trace_id


def _dump(value: Any) -> str:
    """Internal helper kept for symmetry with the serialisation layer."""
    return to_json(value)
