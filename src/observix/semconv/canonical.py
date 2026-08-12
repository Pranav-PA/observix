"""The canonical ``observix.*`` attribute vocabulary.

This is the *only* vocabulary application code ever writes. Dialects translate
it, at export time, into whatever a given backend natively understands.

Keeping this namespace separate from ``gen_ai.*`` is deliberate: the upstream
GenAI semantic conventions are still Development-status and moved repositories
in June 2026. Insulating applications from that churn is a core goal --- when
an upstream attribute is renamed, exactly one dialect changes.
"""

from __future__ import annotations

from typing import Final

#: Prefix owned by observix. Never emitted unless the ``passthrough`` dialect
#: is selected.
NAMESPACE: Final = "observix"

# --- Core --------------------------------------------------------------------

KIND: Final = "observix.kind"
"""Canonical :class:`~observix.model.enums.SpanKind` value."""

NAME: Final = "observix.name"
"""Human-facing operation name, when it differs from the OTel span name."""

INPUT: Final = "observix.input"
"""JSON-encoded operation input (unstructured / non-message)."""

OUTPUT: Final = "observix.output"
"""JSON-encoded operation output (unstructured / non-message)."""

# --- Messages ----------------------------------------------------------------

INPUT_MESSAGES: Final = "observix.input.messages"
"""JSON array of canonical messages sent to the model."""

OUTPUT_MESSAGES: Final = "observix.output.messages"
"""JSON array of canonical messages returned by the model."""

SYSTEM_INSTRUCTIONS: Final = "observix.system_instructions"
"""System prompt, recorded separately from the chat history."""

# --- LLM request -------------------------------------------------------------

LLM_PROVIDER: Final = "observix.llm.provider"
LLM_REQUEST_MODEL: Final = "observix.llm.request.model"
LLM_REQUEST_TEMPERATURE: Final = "observix.llm.request.temperature"
LLM_REQUEST_TOP_P: Final = "observix.llm.request.top_p"
LLM_REQUEST_TOP_K: Final = "observix.llm.request.top_k"
LLM_REQUEST_MAX_TOKENS: Final = "observix.llm.request.max_tokens"
LLM_REQUEST_STOP_SEQUENCES: Final = "observix.llm.request.stop_sequences"
LLM_REQUEST_FREQUENCY_PENALTY: Final = "observix.llm.request.frequency_penalty"
LLM_REQUEST_PRESENCE_PENALTY: Final = "observix.llm.request.presence_penalty"
LLM_REQUEST_SEED: Final = "observix.llm.request.seed"

# --- LLM response ------------------------------------------------------------

LLM_RESPONSE_MODEL: Final = "observix.llm.response.model"
LLM_RESPONSE_ID: Final = "observix.llm.response.id"
LLM_RESPONSE_FINISH_REASONS: Final = "observix.llm.response.finish_reasons"
LLM_STREAMING: Final = "observix.llm.streaming"
LLM_TIME_TO_FIRST_TOKEN_MS: Final = "observix.llm.time_to_first_token_ms"

# --- Usage -------------------------------------------------------------------

USAGE_INPUT_TOKENS: Final = "observix.usage.input_tokens"
USAGE_OUTPUT_TOKENS: Final = "observix.usage.output_tokens"
USAGE_TOTAL_TOKENS: Final = "observix.usage.total_tokens"
USAGE_REASONING_TOKENS: Final = "observix.usage.reasoning_tokens"
USAGE_CACHE_READ_INPUT_TOKENS: Final = "observix.usage.cache_read_input_tokens"
USAGE_CACHE_WRITE_INPUT_TOKENS: Final = "observix.usage.cache_write_input_tokens"

# --- Cost --------------------------------------------------------------------

COST_INPUT_USD: Final = "observix.cost.input_usd"
COST_OUTPUT_USD: Final = "observix.cost.output_usd"
COST_TOTAL_USD: Final = "observix.cost.total_usd"

# --- Tool --------------------------------------------------------------------

TOOL_NAME: Final = "observix.tool.name"
TOOL_DESCRIPTION: Final = "observix.tool.description"
TOOL_CALL_ID: Final = "observix.tool.call_id"
TOOL_ARGUMENTS: Final = "observix.tool.arguments"
TOOL_RESULT: Final = "observix.tool.result"
TOOL_DEFINITIONS: Final = "observix.tool.definitions"

# --- Retrieval ---------------------------------------------------------------

RETRIEVAL_QUERY: Final = "observix.retrieval.query"
RETRIEVAL_DOCUMENTS: Final = "observix.retrieval.documents"
RETRIEVAL_TOP_K: Final = "observix.retrieval.top_k"

# --- Identity ----------------------------------------------------------------

SESSION_ID: Final = "observix.session.id"
USER_ID: Final = "observix.user.id"
CONVERSATION_ID: Final = "observix.conversation.id"
TAGS: Final = "observix.tags"
TRACE_NAME: Final = "observix.trace.name"

# --- Prompt management -------------------------------------------------------

PROMPT_NAME: Final = "observix.prompt.name"
PROMPT_VERSION: Final = "observix.prompt.version"

# --- Free-form ---------------------------------------------------------------

METADATA_PREFIX: Final = "observix.metadata."
"""Arbitrary user metadata. Full key is ``observix.metadata.<name>``."""

# --- Shared / non-observix keys we reuse from OTel core ----------------------

ERROR_TYPE: Final = "error.type"
SERVER_ADDRESS: Final = "server.address"
SERVER_PORT: Final = "server.port"


def metadata_key(name: str) -> str:
    """Build a fully-qualified metadata attribute key."""
    return METADATA_PREFIX + name


def is_content_key(key: str) -> bool:
    """Whether an attribute carries potentially sensitive recorded content.

    Used by the redaction layer to decide what a destination may receive.
    """
    return key in _CONTENT_KEYS


#: Attributes that may contain prompts, completions, documents or tool payloads.
_CONTENT_KEYS: Final = frozenset(
    {
        INPUT,
        OUTPUT,
        INPUT_MESSAGES,
        OUTPUT_MESSAGES,
        SYSTEM_INSTRUCTIONS,
        TOOL_ARGUMENTS,
        TOOL_RESULT,
        TOOL_DEFINITIONS,
        RETRIEVAL_QUERY,
        RETRIEVAL_DOCUMENTS,
    }
)
