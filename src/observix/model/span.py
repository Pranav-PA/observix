"""The :class:`ObservixSpan` facade.

A thin, typed wrapper over an OpenTelemetry span. Every setter writes canonical
``observix.*`` attributes and is individually fail-open --- a bad value produces
a missing attribute, never an exception in the caller.

Design note: content serialisation is *skipped entirely* when no configured
destination retains content. That check happens before the JSON encoder runs,
so a fully-redacted or unconfigured deployment pays nothing for prompts it will
never send.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from opentelemetry.trace import Span as OTelSpan
from opentelemetry.trace import Status, StatusCode
from opentelemetry.util.types import AttributeValue

from .._serde import as_text, to_json
from ..errors import suppress_and_log
from ..semconv import canonical as C
from .enums import SpanKind
from .messages import messages_to_dicts, normalize_messages
from .usage import Cost, TokenUsage

Scalar = str | bool | int | float


def _is_scalar_sequence(value: Any) -> bool:
    """Whether OTel can store ``value`` directly as a homogeneous array."""
    if not isinstance(value, (list, tuple)) or not value:
        return False
    first = type(value[0])
    if first not in (str, bool, int, float):
        return False
    return all(type(v) is first for v in value)


class ObservixSpan:
    """Provider-agnostic handle on an in-flight operation.

    Obtain one from :func:`observix.observe`, :func:`observix.observe_block`,
    :func:`observix.start_span`, or :func:`observix.get_current_span`.
    """

    __slots__ = ("_record_content", "_span")

    def __init__(self, span: OTelSpan, *, record_content: bool = True) -> None:
        self._span = span
        self._record_content = record_content

    # --- Introspection -------------------------------------------------------

    @property
    def otel_span(self) -> OTelSpan:
        """The underlying OpenTelemetry span. Escape hatch for advanced use."""
        return self._span

    @property
    def is_recording(self) -> bool:
        """Whether this span will be exported. ``False`` for sampled-out spans."""
        try:
            return bool(self._span.is_recording())
        except Exception:
            return False

    @property
    def trace_id(self) -> str | None:
        """32-character lowercase hex trace id, or ``None`` if unavailable."""
        try:
            ctx = self._span.get_span_context()
            return format(ctx.trace_id, "032x") if ctx and ctx.trace_id else None
        except Exception:
            return None

    @property
    def span_id(self) -> str | None:
        """16-character lowercase hex span id, or ``None`` if unavailable."""
        try:
            ctx = self._span.get_span_context()
            return format(ctx.span_id, "016x") if ctx and ctx.span_id else None
        except Exception:
            return None

    # --- Primitives ----------------------------------------------------------

    def _set(self, key: str, value: Any) -> None:
        """Write one attribute, coercing to a type OTel accepts."""
        if value is None or not self.is_recording:
            return
        with suppress_and_log(f"ObservixSpan._set({key})"):
            attr: AttributeValue
            if isinstance(value, (str, bool, int, float)):
                attr = value
            elif _is_scalar_sequence(value):
                attr = list(value)
            else:
                attr = to_json(value)
            self._span.set_attribute(key, attr)

    def _set_content(self, key: str, value: Any) -> None:
        """Write a content attribute, skipping serialisation when not retained."""
        if not self._record_content or value is None:
            return
        self._set(key, value)

    def set_attribute(self, key: str, value: Any) -> ObservixSpan:
        """Set a raw attribute. Prefer the typed setters where one exists."""
        self._set(key, value)
        return self

    def set_attributes(self, attributes: Mapping[str, Any]) -> ObservixSpan:
        """Set several raw attributes at once."""
        for key, value in attributes.items():
            self._set(key, value)
        return self

    # --- Core ----------------------------------------------------------------

    def set_kind(self, kind: SpanKind | str) -> ObservixSpan:
        """Set the canonical span kind."""
        self._set(C.KIND, SpanKind.coerce(kind).value)
        return self

    def set_name(self, name: str) -> ObservixSpan:
        """Rename the span. Updates both the OTel name and the canonical one."""
        with suppress_and_log("ObservixSpan.set_name"):
            self._span.update_name(name)
        self._set(C.NAME, name)
        return self

    def set_input(self, value: Any) -> ObservixSpan:
        """Record unstructured operation input."""
        self._set_content(C.INPUT, value)
        return self

    def set_output(self, value: Any) -> ObservixSpan:
        """Record unstructured operation output."""
        self._set_content(C.OUTPUT, value)
        return self

    def set_io(self, *, input: Any = None, output: Any = None) -> ObservixSpan:
        """Record input and output together."""
        self.set_input(input)
        self.set_output(output)
        return self

    # --- Messages ------------------------------------------------------------

    def set_input_messages(self, messages: Any) -> ObservixSpan:
        """Record the conversation sent to the model."""
        if not self._record_content or messages is None:
            return self
        with suppress_and_log("ObservixSpan.set_input_messages"):
            self._set(C.INPUT_MESSAGES, messages_to_dicts(normalize_messages(messages)))
        return self

    def set_output_messages(self, messages: Any) -> ObservixSpan:
        """Record the model's response."""
        if not self._record_content or messages is None:
            return self
        with suppress_and_log("ObservixSpan.set_output_messages"):
            self._set(C.OUTPUT_MESSAGES, messages_to_dicts(normalize_messages(messages)))
        return self

    def set_system_instructions(self, instructions: Any) -> ObservixSpan:
        """Record the system prompt, separately from the chat history."""
        self._set_content(C.SYSTEM_INSTRUCTIONS, as_text(instructions))
        return self

    # --- LLM -----------------------------------------------------------------

    def set_model(
        self,
        *,
        provider: str | None = None,
        request_model: str | None = None,
        response_model: str | None = None,
    ) -> ObservixSpan:
        """Record which model served the call."""
        self._set(C.LLM_PROVIDER, provider)
        self._set(C.LLM_REQUEST_MODEL, request_model)
        self._set(C.LLM_RESPONSE_MODEL, response_model)
        return self

    def set_request_parameters(
        self,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        stop_sequences: Sequence[str] | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        seed: int | None = None,
    ) -> ObservixSpan:
        """Record the sampling parameters of a model call."""
        self._set(C.LLM_REQUEST_TEMPERATURE, temperature)
        self._set(C.LLM_REQUEST_TOP_P, top_p)
        self._set(C.LLM_REQUEST_TOP_K, top_k)
        self._set(C.LLM_REQUEST_MAX_TOKENS, max_tokens)
        if stop_sequences is not None:
            self._set(C.LLM_REQUEST_STOP_SEQUENCES, [str(s) for s in stop_sequences])
        self._set(C.LLM_REQUEST_FREQUENCY_PENALTY, frequency_penalty)
        self._set(C.LLM_REQUEST_PRESENCE_PENALTY, presence_penalty)
        self._set(C.LLM_REQUEST_SEED, seed)
        return self

    def set_response_metadata(
        self,
        *,
        response_id: str | None = None,
        finish_reasons: Sequence[str] | None = None,
        streaming: bool | None = None,
        time_to_first_token_ms: float | None = None,
    ) -> ObservixSpan:
        """Record response-side metadata of a model call."""
        self._set(C.LLM_RESPONSE_ID, response_id)
        if finish_reasons is not None:
            self._set(C.LLM_RESPONSE_FINISH_REASONS, [str(r) for r in finish_reasons])
        self._set(C.LLM_STREAMING, streaming)
        self._set(C.LLM_TIME_TO_FIRST_TOKEN_MS, time_to_first_token_ms)
        return self

    def set_usage(
        self,
        usage: TokenUsage | None = None,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        cache_read_input_tokens: int | None = None,
        cache_write_input_tokens: int | None = None,
    ) -> ObservixSpan:
        """Record token usage, either as a :class:`TokenUsage` or as keywords."""
        if usage is None:
            usage = TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                reasoning_tokens=reasoning_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
                cache_write_input_tokens=cache_write_input_tokens,
            )
        self._set(C.USAGE_INPUT_TOKENS, usage.input_tokens)
        self._set(C.USAGE_OUTPUT_TOKENS, usage.output_tokens)
        self._set(C.USAGE_TOTAL_TOKENS, usage.resolved_total())
        self._set(C.USAGE_REASONING_TOKENS, usage.reasoning_tokens)
        self._set(C.USAGE_CACHE_READ_INPUT_TOKENS, usage.cache_read_input_tokens)
        self._set(C.USAGE_CACHE_WRITE_INPUT_TOKENS, usage.cache_write_input_tokens)
        return self

    def set_cost(
        self,
        cost: Cost | None = None,
        *,
        input_usd: float | None = None,
        output_usd: float | None = None,
        total_usd: float | None = None,
    ) -> ObservixSpan:
        """Record the monetary cost of a model call, in USD."""
        if cost is None:
            cost = Cost(input_usd=input_usd, output_usd=output_usd, total_usd=total_usd)
        self._set(C.COST_INPUT_USD, cost.input_usd)
        self._set(C.COST_OUTPUT_USD, cost.output_usd)
        self._set(C.COST_TOTAL_USD, cost.resolved_total())
        return self

    def record_llm_call(
        self,
        *,
        provider: str | None = None,
        request_model: str | None = None,
        response_model: str | None = None,
        input_messages: Any = None,
        output_messages: Any = None,
        system_instructions: Any = None,
        usage: TokenUsage | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        finish_reasons: Sequence[str] | None = None,
        response_id: str | None = None,
        streaming: bool | None = None,
        kind: SpanKind | str = SpanKind.CHAT,
        **request_parameters: Any,
    ) -> ObservixSpan:
        """Record a complete model call in one call.

        Convenience over the individual setters; cost is computed automatically
        from usage and model when a price book entry exists.
        """
        self.set_kind(kind)
        self.set_model(
            provider=provider, request_model=request_model, response_model=response_model
        )
        self.set_input_messages(input_messages)
        self.set_output_messages(output_messages)
        if system_instructions is not None:
            self.set_system_instructions(system_instructions)
        self.set_request_parameters(
            temperature=temperature, max_tokens=max_tokens, **request_parameters
        )
        self.set_response_metadata(
            response_id=response_id, finish_reasons=finish_reasons, streaming=streaming
        )

        if usage is None and (input_tokens is not None or output_tokens is not None):
            usage = TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
        if usage is not None:
            self.set_usage(usage)
            self._maybe_record_cost(response_model or request_model, provider, usage)
        return self

    def _maybe_record_cost(
        self, model: str | None, provider: str | None, usage: TokenUsage
    ) -> None:
        """Compute cost from the active price book, if one can price this model."""
        if model is None or usage.is_empty():
            return
        with suppress_and_log("ObservixSpan._maybe_record_cost"):
            from ..cost.model import compute_cost

            cost = compute_cost(model=model, provider=provider, usage=usage)
            if cost is not None and not cost.is_empty():
                self.set_cost(cost)

    # --- Tool ----------------------------------------------------------------

    def set_tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        call_id: str | None = None,
        arguments: Any = None,
        result: Any = None,
    ) -> ObservixSpan:
        """Record a tool / function execution."""
        self._set(C.TOOL_NAME, name)
        self._set(C.TOOL_DESCRIPTION, description)
        self._set(C.TOOL_CALL_ID, call_id)
        self._set_content(C.TOOL_ARGUMENTS, arguments)
        self._set_content(C.TOOL_RESULT, result)
        return self

    def set_tool_definitions(self, definitions: Any) -> ObservixSpan:
        """Record the tool schemas offered to the model."""
        self._set_content(C.TOOL_DEFINITIONS, definitions)
        return self

    # --- Retrieval -----------------------------------------------------------

    def set_retrieval(
        self,
        *,
        query: Any = None,
        documents: Iterable[Any] | None = None,
        top_k: int | None = None,
    ) -> ObservixSpan:
        """Record a retrieval step.

        ``documents`` may be strings or ``{id, content, score, metadata}`` dicts.
        """
        self._set_content(C.RETRIEVAL_QUERY, as_text(query) if query is not None else None)
        if documents is not None and self._record_content:
            with suppress_and_log("ObservixSpan.set_retrieval"):
                self._set(C.RETRIEVAL_DOCUMENTS, [_normalize_document(d) for d in documents])
        self._set(C.RETRIEVAL_TOP_K, top_k)
        return self

    # --- Identity ------------------------------------------------------------

    def set_session(
        self,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> ObservixSpan:
        """Attach session / user identity to this span."""
        self._set(C.SESSION_ID, session_id)
        self._set(C.USER_ID, user_id)
        self._set(C.CONVERSATION_ID, conversation_id)
        if tags is not None:
            self._set(C.TAGS, [str(t) for t in tags])
        return self

    def set_prompt(
        self, *, name: str | None = None, version: str | int | None = None
    ) -> ObservixSpan:
        """Link this span to a managed prompt template."""
        self._set(C.PROMPT_NAME, name)
        if version is not None:
            self._set(C.PROMPT_VERSION, str(version))
        return self

    def set_metadata(
        self, _mapping: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> ObservixSpan:
        """Attach arbitrary key/value metadata under ``observix.metadata.*``."""
        items: dict[str, Any] = {}
        if _mapping:
            items.update(_mapping)
        items.update(kwargs)
        for key, value in items.items():
            self._set(C.metadata_key(str(key)), value)
        return self

    # --- Lifecycle -----------------------------------------------------------

    def add_event(self, name: str, attributes: Mapping[str, Any] | None = None) -> ObservixSpan:
        """Add a timestamped event to the span."""
        if not self.is_recording:
            return self
        with suppress_and_log("ObservixSpan.add_event"):
            coerced: dict[str, AttributeValue] = {}
            for key, value in (attributes or {}).items():
                if isinstance(value, (str, bool, int, float)):
                    coerced[key] = value
                elif _is_scalar_sequence(value):
                    coerced[key] = list(value)
                else:
                    coerced[key] = to_json(value)
            self._span.add_event(name, coerced)
        return self

    def record_exception(self, exc: BaseException, *, escaped: bool = True) -> ObservixSpan:
        """Record an exception and mark the span as failed."""
        if not self.is_recording:
            return self
        with suppress_and_log("ObservixSpan.record_exception"):
            self._span.record_exception(exc, escaped=escaped)
            self._span.set_attribute(C.ERROR_TYPE, type(exc).__qualname__)
            self._span.set_status(Status(StatusCode.ERROR, str(exc)))
        return self

    def set_status_ok(self, description: str | None = None) -> ObservixSpan:
        """Mark the span as successful."""
        with suppress_and_log("ObservixSpan.set_status_ok"):
            self._span.set_status(Status(StatusCode.OK, description))
        return self

    def set_status_error(
        self, description: str | None = None, *, error_type: str | None = None
    ) -> ObservixSpan:
        """Mark the span as failed without an exception object."""
        with suppress_and_log("ObservixSpan.set_status_error"):
            self._span.set_status(Status(StatusCode.ERROR, description))
            if error_type:
                self._span.set_attribute(C.ERROR_TYPE, error_type)
        return self

    def end(self) -> None:
        """End the span. Only needed for manually started spans."""
        with suppress_and_log("ObservixSpan.end"):
            self._span.end()

    def __repr__(self) -> str:
        return f"<ObservixSpan trace_id={self.trace_id} span_id={self.span_id}>"


def _normalize_document(doc: Any) -> dict[str, Any]:
    """Coerce a retrieved document into ``{id, content, score, metadata}``."""
    if isinstance(doc, str):
        return {"content": doc}
    if isinstance(doc, Mapping):
        out: dict[str, Any] = {}
        for key, aliases in (
            ("id", ("id", "doc_id", "document_id", "_id")),
            ("content", ("content", "text", "page_content", "chunk")),
            ("score", ("score", "distance", "similarity", "relevance")),
            ("metadata", ("metadata", "meta")),
        ):
            for alias in aliases:
                if alias in doc:
                    out[key] = doc[alias]
                    break
        return out or dict(doc)
    for attr in ("page_content", "text", "content"):
        value = getattr(doc, attr, None)
        if value is not None:
            return {"content": value}
    return {"content": as_text(doc)}


class NoOpSpan(ObservixSpan):
    """A span that records nothing.

    Returned when observix is disabled, so calling code can use the same API
    unconditionally without ``if span is not None`` guards.
    """

    __slots__ = ()

    def __init__(self) -> None:
        from opentelemetry.trace import INVALID_SPAN

        super().__init__(INVALID_SPAN, record_content=False)

    @property
    def is_recording(self) -> bool:
        return False

    def end(self) -> None:
        return None
