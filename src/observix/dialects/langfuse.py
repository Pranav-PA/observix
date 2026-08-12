"""Translate canonical telemetry into Langfuse's ``langfuse.*`` vocabulary.

Langfuse treats its own namespace as highest-precedence and falls back to
inferring from ``gen_ai.*`` / OpenInference / MLflow. Emitting ``langfuse.*``
explicitly is what sidesteps the documented failure where structured GenAI
content arrives but renders as null input/output (langfuse#12657).

Usage and cost go over as ``usage_details`` / ``cost_details`` JSON objects,
which is the shape Langfuse's ingestion expects.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .._serde import to_json
from ..model.enums import SpanKind
from ..semconv import canonical as C
from ..semconv import langfuse as LF
from .base import CanonicalView, Dialect, TranslationResult

_TYPE_MAP: dict[SpanKind, str] = {
    SpanKind.LLM: LF.TYPE_GENERATION,
    SpanKind.CHAT: LF.TYPE_GENERATION,
    SpanKind.EMBEDDING: LF.TYPE_EMBEDDING,
    SpanKind.TOOL: LF.TYPE_TOOL,
    SpanKind.AGENT: LF.TYPE_AGENT,
    SpanKind.WORKFLOW: LF.TYPE_SPAN,
    SpanKind.CHAIN: LF.TYPE_CHAIN,
    SpanKind.RETRIEVER: LF.TYPE_RETRIEVER,
    SpanKind.RERANKER: LF.TYPE_SPAN,
    SpanKind.GUARDRAIL: LF.TYPE_GUARDRAIL,
    SpanKind.TASK: LF.TYPE_SPAN,
    SpanKind.UNKNOWN: LF.TYPE_SPAN,
}

_MODEL_PARAMS: dict[str, str] = {
    C.LLM_REQUEST_TEMPERATURE: "temperature",
    C.LLM_REQUEST_TOP_P: "top_p",
    C.LLM_REQUEST_TOP_K: "top_k",
    C.LLM_REQUEST_MAX_TOKENS: "max_tokens",
    C.LLM_REQUEST_STOP_SEQUENCES: "stop",
    C.LLM_REQUEST_FREQUENCY_PENALTY: "frequency_penalty",
    C.LLM_REQUEST_PRESENCE_PENALTY: "presence_penalty",
    C.LLM_REQUEST_SEED: "seed",
}


class LangfuseDialect(Dialect):
    """Emit ``langfuse.*`` attributes."""

    name: ClassVar[str] = "langfuse"

    def __init__(
        self,
        *,
        capture_content: bool = True,
        release: str | None = None,
        environment: str | None = None,
    ) -> None:
        self.capture_content = capture_content
        self.release = release
        self.environment = environment

    def translate(self, view: CanonicalView) -> TranslationResult:
        result = TranslationResult()
        attrs = view.attributes

        result.set(LF.OBSERVATION_TYPE, _TYPE_MAP.get(view.kind, LF.TYPE_SPAN))
        result.set(LF.OBSERVATION_MODEL_NAME, view.model)

        # Identity is trace-scoped in Langfuse; setting it on any span in the
        # trace is how the platform associates the whole trace.
        result.set(LF.USER_ID, view.user_id)
        result.set(LF.SESSION_ID, view.session_id or view.conversation_id)
        result.set(LF.TRACE_NAME, attrs.get(C.TRACE_NAME))
        tags = view.tags
        if tags:
            result.set(LF.TRACE_TAGS, tags)
        result.set(LF.RELEASE, self.release)
        result.set(LF.ENVIRONMENT, self.environment)

        result.set(LF.OBSERVATION_PROMPT_NAME, attrs.get(C.PROMPT_NAME))
        result.set(LF.OBSERVATION_PROMPT_VERSION, attrs.get(C.PROMPT_VERSION))

        if C.ERROR_TYPE in attrs:
            result.set(LF.OBSERVATION_LEVEL, LF.LEVEL_ERROR)
            result.set(LF.OBSERVATION_STATUS_MESSAGE, attrs[C.ERROR_TYPE])

        self._translate_model_parameters(view, result)
        self._translate_usage(view, result)
        self._translate_metadata(view, result)

        if self.capture_content:
            self._translate_io(view, result)

        return result

    def _translate_model_parameters(self, view: CanonicalView, result: TranslationResult) -> None:
        params: dict[str, Any] = {}
        for source, target in _MODEL_PARAMS.items():
            value = view.get(source)
            if value is not None:
                params[target] = list(value) if isinstance(value, tuple) else value
        if params:
            result.set(LF.OBSERVATION_MODEL_PARAMETERS, to_json(params))

    def _translate_usage(self, view: CanonicalView, result: TranslationResult) -> None:
        usage = view.usage
        details: dict[str, int] = {}
        if usage.input_tokens is not None:
            details["input"] = usage.input_tokens
        if usage.output_tokens is not None:
            details["output"] = usage.output_tokens
        total = usage.resolved_total()
        if total is not None:
            details["total"] = total
        if usage.cache_read_input_tokens is not None:
            details["cache_read_input_tokens"] = usage.cache_read_input_tokens
        if usage.cache_write_input_tokens is not None:
            details["cache_creation_input_tokens"] = usage.cache_write_input_tokens
        if usage.reasoning_tokens is not None:
            details["reasoning"] = usage.reasoning_tokens
        if details:
            result.set(LF.OBSERVATION_USAGE_DETAILS, to_json(details))

        cost = view.cost
        cost_details: dict[str, float] = {}
        if cost.input_usd is not None:
            cost_details["input"] = cost.input_usd
        if cost.output_usd is not None:
            cost_details["output"] = cost.output_usd
        cost_total = cost.resolved_total()
        if cost_total is not None:
            cost_details["total"] = cost_total
        if cost_details:
            result.set(LF.OBSERVATION_COST_DETAILS, to_json(cost_details))

    def _translate_metadata(self, view: CanonicalView, result: TranslationResult) -> None:
        for key, value in view.metadata.items():
            result.set(LF.observation_metadata_key(key), value)

    def _translate_io(self, view: CanonicalView, result: TranslationResult) -> None:
        raw_in = view.resolved_input()
        if raw_in is None and view.get(C.TOOL_ARGUMENTS) is not None:
            raw_in = view.get(C.TOOL_ARGUMENTS)
        if raw_in is None and view.get(C.RETRIEVAL_QUERY) is not None:
            raw_in = view.get(C.RETRIEVAL_QUERY)

        raw_out = view.resolved_output()
        if raw_out is None and view.get(C.TOOL_RESULT) is not None:
            raw_out = view.get(C.TOOL_RESULT)
        if raw_out is None and view.get(C.RETRIEVAL_DOCUMENTS) is not None:
            raw_out = view.get(C.RETRIEVAL_DOCUMENTS)

        if raw_in is not None:
            result.set(LF.OBSERVATION_INPUT, raw_in if isinstance(raw_in, str) else to_json(raw_in))
        if raw_out is not None:
            result.set(
                LF.OBSERVATION_OUTPUT, raw_out if isinstance(raw_out, str) else to_json(raw_out)
            )

        system = view.system_instructions
        if system:
            result.set(LF.observation_metadata_key("system_instructions"), system)
