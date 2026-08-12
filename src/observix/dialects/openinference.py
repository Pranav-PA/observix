"""Translate canonical telemetry into OpenInference (Arize Phoenix / Arize AX).

OpenInference flattens list-valued data into indexed attribute keys, so a
three-message conversation becomes nine attributes. That is what Phoenix's UI
reads, and emitting a single JSON blob instead produces a trace that renders
but cannot be inspected message-by-message.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .._serde import from_json, to_json
from ..model.enums import PartType, SpanKind
from ..model.messages import Message
from ..semconv import canonical as C
from ..semconv import openinference as OI
from .base import CanonicalView, Dialect, TranslationResult

_KIND_MAP: dict[SpanKind, str] = {
    SpanKind.LLM: OI.KIND_LLM,
    SpanKind.CHAT: OI.KIND_LLM,
    SpanKind.EMBEDDING: OI.KIND_EMBEDDING,
    SpanKind.TOOL: OI.KIND_TOOL,
    SpanKind.AGENT: OI.KIND_AGENT,
    SpanKind.WORKFLOW: OI.KIND_CHAIN,
    SpanKind.CHAIN: OI.KIND_CHAIN,
    SpanKind.RETRIEVER: OI.KIND_RETRIEVER,
    SpanKind.RERANKER: OI.KIND_RERANKER,
    SpanKind.GUARDRAIL: OI.KIND_GUARDRAIL,
    SpanKind.TASK: OI.KIND_CHAIN,
    SpanKind.UNKNOWN: OI.KIND_UNKNOWN,
}

#: Canonical request parameters collapse into one JSON blob in OpenInference.
_INVOCATION_PARAMS: dict[str, str] = {
    C.LLM_REQUEST_TEMPERATURE: "temperature",
    C.LLM_REQUEST_TOP_P: "top_p",
    C.LLM_REQUEST_TOP_K: "top_k",
    C.LLM_REQUEST_MAX_TOKENS: "max_tokens",
    C.LLM_REQUEST_STOP_SEQUENCES: "stop",
    C.LLM_REQUEST_FREQUENCY_PENALTY: "frequency_penalty",
    C.LLM_REQUEST_PRESENCE_PENALTY: "presence_penalty",
    C.LLM_REQUEST_SEED: "seed",
}


class OpenInferenceDialect(Dialect):
    """Emit OpenInference attributes."""

    name: ClassVar[str] = "openinference"

    def __init__(self, *, capture_content: bool = True, max_messages: int = 128) -> None:
        self.capture_content = capture_content
        self.max_messages = max_messages

    def translate(self, view: CanonicalView) -> TranslationResult:
        result = TranslationResult()
        attrs = view.attributes
        kind = view.kind

        result.set(OI.SPAN_KIND, _KIND_MAP.get(kind, OI.KIND_CHAIN))

        model = view.model
        if kind is SpanKind.EMBEDDING:
            result.set(OI.EMBEDDING_MODEL_NAME, model)
        else:
            result.set(OI.LLM_MODEL_NAME, model)
        result.set(OI.LLM_PROVIDER, view.provider)
        result.set(OI.LLM_SYSTEM, view.provider)

        self._translate_invocation_parameters(view, result)
        self._translate_usage(view, result)
        self._translate_identity(view, result)

        if self.capture_content:
            self._translate_io(view, result)
            self._translate_messages(view, result)
            self._translate_tool(view, result)
            self._translate_documents(view, result)

        result.set(OI.PROMPT_TEMPLATE_VERSION, attrs.get(C.PROMPT_VERSION))
        return result

    # --- Sections ------------------------------------------------------------

    def _translate_invocation_parameters(
        self, view: CanonicalView, result: TranslationResult
    ) -> None:
        params: dict[str, Any] = {}
        for source, target in _INVOCATION_PARAMS.items():
            value = view.get(source)
            if value is not None:
                params[target] = list(value) if isinstance(value, tuple) else value
        if params:
            result.set(OI.LLM_INVOCATION_PARAMETERS, to_json(params))

    def _translate_usage(self, view: CanonicalView, result: TranslationResult) -> None:
        usage = view.usage
        result.set(OI.LLM_TOKEN_COUNT_PROMPT, usage.input_tokens)
        result.set(OI.LLM_TOKEN_COUNT_COMPLETION, usage.output_tokens)
        result.set(OI.LLM_TOKEN_COUNT_TOTAL, usage.resolved_total())
        result.set(OI.LLM_TOKEN_COUNT_PROMPT_CACHE_HIT, usage.cache_read_input_tokens)
        result.set(OI.LLM_TOKEN_COUNT_PROMPT_CACHE_WRITE, usage.cache_write_input_tokens)
        result.set(OI.LLM_TOKEN_COUNT_COMPLETION_REASONING, usage.reasoning_tokens)

        cost = view.cost
        result.set(OI.LLM_COST_PROMPT, cost.input_usd)
        result.set(OI.LLM_COST_COMPLETION, cost.output_usd)
        result.set(OI.LLM_COST_TOTAL, cost.resolved_total())

    def _translate_identity(self, view: CanonicalView, result: TranslationResult) -> None:
        result.set(OI.SESSION_ID, view.session_id or view.conversation_id)
        result.set(OI.USER_ID, view.user_id)
        tags = view.tags
        if tags:
            result.set(OI.TAG_TAGS, tags)
        metadata = view.metadata
        if metadata:
            result.set(OI.METADATA, to_json(metadata))

    def _translate_io(self, view: CanonicalView, result: TranslationResult) -> None:
        raw_in = view.resolved_input()
        if raw_in is not None:
            result.set(OI.INPUT_VALUE, raw_in if isinstance(raw_in, str) else to_json(raw_in))
            result.set(OI.INPUT_MIME_TYPE, _mime_for(raw_in))

        raw_out = view.resolved_output()
        if raw_out is not None:
            result.set(OI.OUTPUT_VALUE, raw_out if isinstance(raw_out, str) else to_json(raw_out))
            result.set(OI.OUTPUT_MIME_TYPE, _mime_for(raw_out))

    def _translate_messages(self, view: CanonicalView, result: TranslationResult) -> None:
        self._flatten_messages(view.input_messages, OI.LLM_INPUT_MESSAGES, result)
        self._flatten_messages(view.output_messages, OI.LLM_OUTPUT_MESSAGES, result)

        system = view.system_instructions
        if system:
            result.set(OI.PROMPT_TEMPLATE_TEMPLATE, system)

    def _flatten_messages(
        self, messages: list[Message], base: str, result: TranslationResult
    ) -> None:
        for index, message in enumerate(messages[: self.max_messages]):
            result.set(OI.message_key(base, index, OI.MESSAGE_ROLE), message.role)

            text = message.text()
            if text:
                result.set(OI.message_key(base, index, OI.MESSAGE_CONTENT), text)

            calls = [p for p in message.parts if p.type == PartType.TOOL_CALL.value]
            for call_index, part in enumerate(calls):
                prefix = OI.message_key(base, index, f"{OI.MESSAGE_TOOL_CALLS}.{call_index}")
                result.set(f"{prefix}.{OI.TOOL_CALL_ID}", part.id)
                result.set(f"{prefix}.{OI.TOOL_CALL_FUNCTION_NAME}", part.name)
                if part.content is not None:
                    arguments = (
                        part.content if isinstance(part.content, str) else to_json(part.content)
                    )
                    result.set(f"{prefix}.{OI.TOOL_CALL_FUNCTION_ARGUMENTS}", arguments)

    def _translate_tool(self, view: CanonicalView, result: TranslationResult) -> None:
        result.set(OI.TOOL_NAME, view.get(C.TOOL_NAME))
        result.set(OI.TOOL_DESCRIPTION, view.get(C.TOOL_DESCRIPTION))
        arguments = view.get(C.TOOL_ARGUMENTS)
        if arguments is not None:
            result.set(
                OI.TOOL_PARAMETERS,
                arguments if isinstance(arguments, str) else to_json(arguments),
            )
        definitions = view.get(C.TOOL_DEFINITIONS)
        if definitions is not None:
            result.set(
                OI.LLM_TOOLS,
                definitions if isinstance(definitions, str) else to_json(definitions),
            )

    def _translate_documents(self, view: CanonicalView, result: TranslationResult) -> None:
        raw = view.get(C.RETRIEVAL_DOCUMENTS)
        documents = from_json(raw) if isinstance(raw, str) else raw
        if not isinstance(documents, (list, tuple)):
            return
        for index, doc in enumerate(documents[: self.max_messages]):
            if not isinstance(doc, dict):
                result.set(OI.document_key(index, OI.DOCUMENT_CONTENT), str(doc))
                continue
            result.set(OI.document_key(index, OI.DOCUMENT_ID), _opt_str(doc.get("id")))
            content = doc.get("content")
            if content is not None:
                result.set(
                    OI.document_key(index, OI.DOCUMENT_CONTENT),
                    content if isinstance(content, str) else to_json(content),
                )
            score = doc.get("score")
            if isinstance(score, (int, float)):
                result.set(OI.document_key(index, OI.DOCUMENT_SCORE), float(score))
            metadata = doc.get("metadata")
            if metadata is not None:
                result.set(OI.document_key(index, OI.DOCUMENT_METADATA), to_json(metadata))


def _mime_for(value: Any) -> str:
    """Classify content for Phoenix's viewer.

    Canonical attributes store structured data as JSON *strings*, so a plain
    ``isinstance(value, str)`` test would mislabel every message list as
    ``text/plain`` and cost the reader Phoenix's structured view.
    """
    if not isinstance(value, str):
        return OI.MIME_JSON
    stripped = value.lstrip()
    return OI.MIME_JSON if stripped.startswith(("{", "[")) else OI.MIME_TEXT


def _opt_str(value: Any) -> Any:
    return None if value is None else str(value)
