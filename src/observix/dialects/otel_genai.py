"""Translate canonical telemetry into OpenTelemetry GenAI semantic conventions.

The default dialect for generic OTLP backends (Grafana, Jaeger, Honeycomb,
SigNoz, Datadog, ...).

One deliberate deviation from a strict reading of the spec: the structured
``gen_ai.input.messages`` / ``gen_ai.output.messages`` attributes are emitted
*alongside* the legacy flat ``gen_ai.prompt`` / ``gen_ai.completion`` keys.
Several backends still only read the flat form, and emitting both is the
difference between visible content and a blank panel.
"""

from __future__ import annotations

from typing import ClassVar

from .._serde import to_json
from ..model.enums import SpanKind
from ..semconv import canonical as C
from ..semconv import genai as G
from .base import CanonicalView, Dialect, TranslationResult

#: Canonical kind -> gen_ai.operation.name
_OPERATION_BY_KIND: dict[SpanKind, str] = {
    SpanKind.LLM: G.OP_TEXT_COMPLETION,
    SpanKind.CHAT: G.OP_CHAT,
    SpanKind.EMBEDDING: G.OP_EMBEDDINGS,
    SpanKind.TOOL: G.OP_EXECUTE_TOOL,
    SpanKind.AGENT: G.OP_INVOKE_AGENT,
    SpanKind.RETRIEVER: G.OP_RETRIEVAL,
}

#: Straight canonical -> gen_ai renames with no value transformation.
_DIRECT: dict[str, str] = {
    C.LLM_REQUEST_MODEL: G.REQUEST_MODEL,
    C.LLM_REQUEST_TEMPERATURE: G.REQUEST_TEMPERATURE,
    C.LLM_REQUEST_TOP_P: G.REQUEST_TOP_P,
    C.LLM_REQUEST_TOP_K: G.REQUEST_TOP_K,
    C.LLM_REQUEST_MAX_TOKENS: G.REQUEST_MAX_TOKENS,
    C.LLM_REQUEST_STOP_SEQUENCES: G.REQUEST_STOP_SEQUENCES,
    C.LLM_REQUEST_FREQUENCY_PENALTY: G.REQUEST_FREQUENCY_PENALTY,
    C.LLM_REQUEST_PRESENCE_PENALTY: G.REQUEST_PRESENCE_PENALTY,
    C.LLM_REQUEST_SEED: G.REQUEST_SEED,
    C.LLM_RESPONSE_MODEL: G.RESPONSE_MODEL,
    C.LLM_RESPONSE_ID: G.RESPONSE_ID,
    C.LLM_RESPONSE_FINISH_REASONS: G.RESPONSE_FINISH_REASONS,
    C.LLM_TIME_TO_FIRST_TOKEN_MS: G.RESPONSE_TIME_TO_FIRST_CHUNK,
    C.USAGE_INPUT_TOKENS: G.USAGE_INPUT_TOKENS,
    C.USAGE_OUTPUT_TOKENS: G.USAGE_OUTPUT_TOKENS,
    C.USAGE_CACHE_READ_INPUT_TOKENS: G.USAGE_CACHE_READ_INPUT_TOKENS,
    C.USAGE_CACHE_WRITE_INPUT_TOKENS: G.USAGE_CACHE_CREATION_INPUT_TOKENS,
    C.CONVERSATION_ID: G.CONVERSATION_ID,
    C.SYSTEM_INSTRUCTIONS: G.SYSTEM_INSTRUCTIONS,
    C.TOOL_NAME: G.TOOL_NAME,
    C.TOOL_DESCRIPTION: G.TOOL_DESCRIPTION,
    C.TOOL_CALL_ID: G.TOOL_CALL_ID,
    C.TOOL_ARGUMENTS: G.TOOL_CALL_ARGUMENTS,
    C.TOOL_RESULT: G.TOOL_CALL_RESULT,
    C.TOOL_DEFINITIONS: G.TOOL_DEFINITIONS,
}


class OTelGenAIDialect(Dialect):
    """Emit ``gen_ai.*`` attributes."""

    name: ClassVar[str] = "otel_genai"

    #: When ``False``, prompts and completions are omitted entirely, matching
    #: the spec's opt-in stance on content capture.
    def __init__(self, *, capture_content: bool = True, legacy_content: bool = True) -> None:
        self.capture_content = capture_content
        self.legacy_content = legacy_content

    def translate(self, view: CanonicalView) -> TranslationResult:
        result = TranslationResult()
        attrs = view.attributes

        operation = _OPERATION_BY_KIND.get(view.kind)
        result.set(G.OPERATION_NAME, operation)
        result.set(G.PROVIDER_NAME, view.provider)

        for source, target in _DIRECT.items():
            if source in attrs:
                result.set(target, attrs[source])

        if view.kind is SpanKind.AGENT:
            result.set(G.AGENT_NAME, attrs.get(C.NAME) or view.span_name)

        if self.capture_content:
            self._translate_content(view, result)

        # gen_ai has no cost or session attributes; keep them under observix.*
        # rather than inventing names, so nothing is silently lost.
        for key in (
            C.COST_INPUT_USD,
            C.COST_OUTPUT_USD,
            C.COST_TOTAL_USD,
            C.SESSION_ID,
            C.USER_ID,
            C.TAGS,
            C.USAGE_TOTAL_TOKENS,
            C.USAGE_REASONING_TOKENS,
            C.PROMPT_NAME,
            C.PROMPT_VERSION,
        ):
            if key in attrs:
                result.set(key, attrs[key])
        for key, value in attrs.items():
            if key.startswith(C.METADATA_PREFIX):
                result.set(key, value)

        result.name = self._span_name(view, operation)
        return result

    def _translate_content(self, view: CanonicalView, result: TranslationResult) -> None:
        attrs = view.attributes

        if C.INPUT_MESSAGES in attrs:
            result.set(G.INPUT_MESSAGES, attrs[C.INPUT_MESSAGES])
        if C.OUTPUT_MESSAGES in attrs:
            result.set(G.OUTPUT_MESSAGES, attrs[C.OUTPUT_MESSAGES])

        if self.legacy_content:
            # Flat form for backends that never adopted the structured keys.
            result.set(G.LEGACY_PROMPT, view.input_text())
            result.set(G.LEGACY_COMPLETION, view.output_text())
        else:
            result.set(G.LEGACY_PROMPT, None)

        # Non-message I/O has no gen_ai home; preserve canonically.
        if C.INPUT in attrs and C.INPUT_MESSAGES not in attrs:
            result.set(C.INPUT, attrs[C.INPUT])
        if C.OUTPUT in attrs and C.OUTPUT_MESSAGES not in attrs:
            result.set(C.OUTPUT, attrs[C.OUTPUT])

        if C.RETRIEVAL_QUERY in attrs:
            result.set(C.RETRIEVAL_QUERY, attrs[C.RETRIEVAL_QUERY])
        if C.RETRIEVAL_DOCUMENTS in attrs:
            docs = attrs[C.RETRIEVAL_DOCUMENTS]
            result.set(C.RETRIEVAL_DOCUMENTS, docs if isinstance(docs, str) else to_json(docs))

    @staticmethod
    def _span_name(view: CanonicalView, operation: str | None) -> str | None:
        """Follow the spec's ``{operation} {model}`` naming where we can."""
        if operation is None:
            return None
        if view.kind is SpanKind.TOOL:
            tool = view.get(C.TOOL_NAME)
            return f"{operation} {tool}" if tool else operation
        model = view.request_model or view.response_model
        return f"{operation} {model}" if model else operation
