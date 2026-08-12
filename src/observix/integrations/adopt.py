"""Adopt spans emitted by other instrumentation libraries.

An application already instrumented with OpenLLMetry, OpenInference or OpenLIT
emits spans in *one* vocabulary. This module maps those inbound attributes back
onto observix's canonical model, so those spans then flow through the same
dialect pipeline as ``@observe`` spans --- gaining multi-backend fan-out and
per-destination redaction with no re-instrumentation.

Enable it globally:

    configure(exporters=["phoenix", "langfuse"], adopt_foreign=True)

The mapping is deliberately conservative. Anything already carrying an
``observix.*`` attribute is left alone, and a canonical key that is already
present is never overwritten --- adoption only ever *adds* information.
"""

from __future__ import annotations

import json
from typing import Any

from .._serde import from_json
from ..model.enums import SpanKind
from ..semconv import canonical as C
from ..semconv import genai as G
from ..semconv import mlflow as MF
from ..semconv import openinference as OI

# --- Span-kind inference ------------------------------------------------------

#: OpenInference span kinds -> canonical kinds.
_OI_KIND: dict[str, SpanKind] = {
    OI.KIND_LLM: SpanKind.CHAT,
    OI.KIND_CHAIN: SpanKind.CHAIN,
    OI.KIND_TOOL: SpanKind.TOOL,
    OI.KIND_RETRIEVER: SpanKind.RETRIEVER,
    OI.KIND_RERANKER: SpanKind.RERANKER,
    OI.KIND_EMBEDDING: SpanKind.EMBEDDING,
    OI.KIND_AGENT: SpanKind.AGENT,
    OI.KIND_GUARDRAIL: SpanKind.GUARDRAIL,
}

#: gen_ai.operation.name -> canonical kinds.
_GENAI_KIND: dict[str, SpanKind] = {
    G.OP_CHAT: SpanKind.CHAT,
    G.OP_TEXT_COMPLETION: SpanKind.LLM,
    G.OP_GENERATE_CONTENT: SpanKind.LLM,
    G.OP_EMBEDDINGS: SpanKind.EMBEDDING,
    G.OP_EXECUTE_TOOL: SpanKind.TOOL,
    G.OP_INVOKE_AGENT: SpanKind.AGENT,
    G.OP_CREATE_AGENT: SpanKind.AGENT,
    G.OP_RETRIEVAL: SpanKind.RETRIEVER,
}

#: MLflow span types -> canonical kinds.
_MLFLOW_KIND: dict[str, SpanKind] = {
    MF.TYPE_LLM: SpanKind.LLM,
    MF.TYPE_CHAT_MODEL: SpanKind.CHAT,
    MF.TYPE_EMBEDDING: SpanKind.EMBEDDING,
    MF.TYPE_TOOL: SpanKind.TOOL,
    MF.TYPE_AGENT: SpanKind.AGENT,
    MF.TYPE_CHAIN: SpanKind.CHAIN,
    MF.TYPE_RETRIEVER: SpanKind.RETRIEVER,
    MF.TYPE_RERANKER: SpanKind.RERANKER,
}

# --- Direct attribute mappings ------------------------------------------------

#: Foreign key -> canonical key. Earlier entries win when several are present.
_DIRECT: tuple[tuple[str, str], ...] = (
    # OpenTelemetry GenAI
    (G.REQUEST_MODEL, C.LLM_REQUEST_MODEL),
    (G.RESPONSE_MODEL, C.LLM_RESPONSE_MODEL),
    (G.PROVIDER_NAME, C.LLM_PROVIDER),
    ("gen_ai.system", C.LLM_PROVIDER),  # pre-1.36 name, still widely emitted
    (G.REQUEST_TEMPERATURE, C.LLM_REQUEST_TEMPERATURE),
    (G.REQUEST_TOP_P, C.LLM_REQUEST_TOP_P),
    (G.REQUEST_TOP_K, C.LLM_REQUEST_TOP_K),
    (G.REQUEST_MAX_TOKENS, C.LLM_REQUEST_MAX_TOKENS),
    (G.REQUEST_STOP_SEQUENCES, C.LLM_REQUEST_STOP_SEQUENCES),
    (G.REQUEST_SEED, C.LLM_REQUEST_SEED),
    (G.RESPONSE_ID, C.LLM_RESPONSE_ID),
    (G.RESPONSE_FINISH_REASONS, C.LLM_RESPONSE_FINISH_REASONS),
    (G.USAGE_INPUT_TOKENS, C.USAGE_INPUT_TOKENS),
    (G.USAGE_OUTPUT_TOKENS, C.USAGE_OUTPUT_TOKENS),
    (G.USAGE_CACHE_READ_INPUT_TOKENS, C.USAGE_CACHE_READ_INPUT_TOKENS),
    (G.USAGE_CACHE_CREATION_INPUT_TOKENS, C.USAGE_CACHE_WRITE_INPUT_TOKENS),
    (G.CONVERSATION_ID, C.CONVERSATION_ID),
    (G.SYSTEM_INSTRUCTIONS, C.SYSTEM_INSTRUCTIONS),
    (G.INPUT_MESSAGES, C.INPUT_MESSAGES),
    (G.OUTPUT_MESSAGES, C.OUTPUT_MESSAGES),
    (G.LEGACY_PROMPT, C.INPUT),
    (G.LEGACY_COMPLETION, C.OUTPUT),
    (G.TOOL_NAME, C.TOOL_NAME),
    (G.TOOL_DESCRIPTION, C.TOOL_DESCRIPTION),
    (G.TOOL_CALL_ID, C.TOOL_CALL_ID),
    (G.TOOL_CALL_ARGUMENTS, C.TOOL_ARGUMENTS),
    (G.TOOL_CALL_RESULT, C.TOOL_RESULT),
    # OpenInference
    (OI.LLM_MODEL_NAME, C.LLM_REQUEST_MODEL),
    (OI.EMBEDDING_MODEL_NAME, C.LLM_REQUEST_MODEL),
    (OI.LLM_PROVIDER, C.LLM_PROVIDER),
    (OI.LLM_SYSTEM, C.LLM_PROVIDER),
    (OI.INPUT_VALUE, C.INPUT),
    (OI.OUTPUT_VALUE, C.OUTPUT),
    (OI.LLM_TOKEN_COUNT_PROMPT, C.USAGE_INPUT_TOKENS),
    (OI.LLM_TOKEN_COUNT_COMPLETION, C.USAGE_OUTPUT_TOKENS),
    (OI.LLM_TOKEN_COUNT_TOTAL, C.USAGE_TOTAL_TOKENS),
    (OI.LLM_TOKEN_COUNT_PROMPT_CACHE_HIT, C.USAGE_CACHE_READ_INPUT_TOKENS),
    (OI.LLM_COST_PROMPT, C.COST_INPUT_USD),
    (OI.LLM_COST_COMPLETION, C.COST_OUTPUT_USD),
    (OI.LLM_COST_TOTAL, C.COST_TOTAL_USD),
    (OI.SESSION_ID, C.SESSION_ID),
    (OI.USER_ID, C.USER_ID),
    (OI.TOOL_NAME, C.TOOL_NAME),
    (OI.TOOL_DESCRIPTION, C.TOOL_DESCRIPTION),
    (OI.TOOL_PARAMETERS, C.TOOL_ARGUMENTS),
    # MLflow
    (MF.SPAN_INPUTS, C.INPUT),
    (MF.SPAN_OUTPUTS, C.OUTPUT),
    (MF.LLM_MODEL, C.LLM_REQUEST_MODEL),
    (MF.LLM_PROVIDER, C.LLM_PROVIDER),
    (MF.TRACE_SESSION, C.SESSION_ID),
    (MF.TRACE_USER, C.USER_ID),
    # Traceloop / OpenLLMetry extras
    ("traceloop.entity.name", C.NAME),
    ("traceloop.workflow.name", C.TRACE_NAME),
    # Plain OTel conventions some libraries use directly
    ("session.id", C.SESSION_ID),
    ("user.id", C.USER_ID),
)


def looks_foreign(attributes: dict[str, Any]) -> bool:
    """Whether a span appears to come from another instrumentation library."""
    if any(key.startswith(C.NAMESPACE + ".") for key in attributes):
        return False
    return any(
        key.startswith(("gen_ai.", "llm.", "openinference.", "mlflow.", "traceloop."))
        or key in (OI.INPUT_VALUE, OI.OUTPUT_VALUE)
        for key in attributes
    )


def infer_kind(attributes: dict[str, Any]) -> SpanKind | None:
    """Infer a canonical span kind from foreign attributes."""
    oi_kind = attributes.get(OI.SPAN_KIND)
    if isinstance(oi_kind, str):
        found = _OI_KIND.get(oi_kind.strip().upper())
        if found is not None:
            return found

    operation = attributes.get(G.OPERATION_NAME)
    if isinstance(operation, str):
        found = _GENAI_KIND.get(operation.strip().lower())
        if found is not None:
            return found

    mlflow_type = attributes.get(MF.SPAN_TYPE)
    if isinstance(mlflow_type, str):
        found = _MLFLOW_KIND.get(mlflow_type.strip().upper())
        if found is not None:
            return found

    # A span carrying token counts is a model call even if it says nothing else.
    if any(k in attributes for k in (G.USAGE_INPUT_TOKENS, OI.LLM_TOKEN_COUNT_PROMPT)):
        return SpanKind.CHAT
    return None


def _unflatten_openinference_messages(
    attributes: dict[str, Any], base: str
) -> list[dict[str, Any]] | None:
    """Rebuild canonical messages from OpenInference's indexed attribute keys."""
    prefix = base + "."
    by_index: dict[int, dict[str, Any]] = {}

    for key, value in attributes.items():
        if not key.startswith(prefix):
            continue
        remainder = key[len(prefix) :]
        index_text, _, field = remainder.partition(".")
        if not index_text.isdigit():
            continue
        entry = by_index.setdefault(int(index_text), {})
        if field == OI.MESSAGE_ROLE:
            entry["role"] = str(value)
        elif field == OI.MESSAGE_CONTENT:
            entry["content"] = value

    if not by_index:
        return None
    return [
        {
            "role": by_index[i].get("role", "user"),
            "parts": [{"type": "text", "content": by_index[i].get("content")}],
        }
        for i in sorted(by_index)
    ]


def normalize_foreign_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Add canonical attributes derived from foreign ones.

    Returns a new mapping. Foreign attributes are preserved, so a destination
    that understood them already continues to. Canonical keys already present
    are never overwritten.
    """
    if not looks_foreign(attributes):
        return attributes

    out = dict(attributes)

    kind = infer_kind(attributes)
    if kind is not None:
        out.setdefault(C.KIND, kind.value)

    for foreign_key, canonical_key in _DIRECT:
        if foreign_key in attributes and canonical_key not in out:
            out[canonical_key] = attributes[foreign_key]

    for base, canonical_key in (
        (OI.LLM_INPUT_MESSAGES, C.INPUT_MESSAGES),
        (OI.LLM_OUTPUT_MESSAGES, C.OUTPUT_MESSAGES),
    ):
        if canonical_key not in out:
            messages = _unflatten_openinference_messages(attributes, base)
            if messages:
                out[canonical_key] = json.dumps(messages, ensure_ascii=False)

    # MLflow packs usage into a JSON object.
    raw_usage = attributes.get(MF.CHAT_USAGE)
    if isinstance(raw_usage, str):
        usage = from_json(raw_usage)
        if isinstance(usage, dict):
            for source, target in (
                ("input_tokens", C.USAGE_INPUT_TOKENS),
                ("output_tokens", C.USAGE_OUTPUT_TOKENS),
                ("total_tokens", C.USAGE_TOTAL_TOKENS),
            ):
                if source in usage and target not in out:
                    out[target] = usage[source]

    return out
