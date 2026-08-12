"""Translate canonical telemetry into MLflow's tracing vocabulary."""

from __future__ import annotations

from typing import ClassVar

from .._serde import to_json
from ..model.enums import SpanKind
from ..semconv import canonical as C
from ..semconv import mlflow as MF
from .base import CanonicalView, Dialect, TranslationResult

_TYPE_MAP: dict[SpanKind, str] = {
    SpanKind.LLM: MF.TYPE_LLM,
    SpanKind.CHAT: MF.TYPE_CHAT_MODEL,
    SpanKind.EMBEDDING: MF.TYPE_EMBEDDING,
    SpanKind.TOOL: MF.TYPE_TOOL,
    SpanKind.AGENT: MF.TYPE_AGENT,
    SpanKind.WORKFLOW: MF.TYPE_CHAIN,
    SpanKind.CHAIN: MF.TYPE_CHAIN,
    SpanKind.RETRIEVER: MF.TYPE_RETRIEVER,
    SpanKind.RERANKER: MF.TYPE_RERANKER,
    SpanKind.GUARDRAIL: MF.TYPE_UNKNOWN,
    SpanKind.TASK: MF.TYPE_UNKNOWN,
    SpanKind.UNKNOWN: MF.TYPE_UNKNOWN,
}


class MLflowDialect(Dialect):
    """Emit ``mlflow.*`` attributes.

    MLflow requires ``spanInputs`` / ``spanOutputs`` to be JSON-encoded, so
    string content is wrapped rather than passed through raw.
    """

    name: ClassVar[str] = "mlflow"

    def __init__(self, *, capture_content: bool = True) -> None:
        self.capture_content = capture_content

    def translate(self, view: CanonicalView) -> TranslationResult:
        result = TranslationResult()
        attrs = view.attributes

        result.set(MF.SPAN_TYPE, _TYPE_MAP.get(view.kind, MF.TYPE_UNKNOWN))
        result.set(MF.LLM_MODEL, view.model)
        result.set(MF.LLM_PROVIDER, view.provider)
        result.set(MF.SPAN_FUNCTION_NAME, attrs.get(C.NAME) or view.span_name or None)

        usage = view.usage
        if not usage.is_empty():
            details = {}
            if usage.input_tokens is not None:
                details["input_tokens"] = usage.input_tokens
            if usage.output_tokens is not None:
                details["output_tokens"] = usage.output_tokens
            total = usage.resolved_total()
            if total is not None:
                details["total_tokens"] = total
            if details:
                result.set(MF.CHAT_USAGE, to_json(details))

        result.set(MF.TRACE_SESSION, view.session_id or view.conversation_id)
        result.set(MF.TRACE_USER, view.user_id)

        if self.capture_content:
            raw_in = view.resolved_input()
            if raw_in is None:
                raw_in = view.get(C.TOOL_ARGUMENTS) or view.get(C.RETRIEVAL_QUERY)
            raw_out = view.resolved_output()
            if raw_out is None:
                raw_out = view.get(C.TOOL_RESULT) or view.get(C.RETRIEVAL_DOCUMENTS)

            if raw_in is not None:
                result.set(MF.SPAN_INPUTS, _as_json(raw_in))
            if raw_out is not None:
                result.set(MF.SPAN_OUTPUTS, _as_json(raw_out))

        # MLflow has no cost namespace; keep canonical keys so data survives.
        for key in (C.COST_INPUT_USD, C.COST_OUTPUT_USD, C.COST_TOTAL_USD):
            if key in attrs:
                result.set(key, attrs[key])
        for key, value in attrs.items():
            if key.startswith(C.METADATA_PREFIX):
                result.set(key, value)

        return result


def _as_json(value: object) -> str:
    """MLflow expects JSON; a bare string must still be valid JSON."""
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            return value
        return to_json(value)
    return to_json(value)
