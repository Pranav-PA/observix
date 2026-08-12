"""OpenInference semantic conventions (Arize Phoenix / Arize AX).

List-valued data is flattened with zero-based indices, e.g.
``llm.input_messages.0.message.role`` --- see :func:`message_key`.
"""

from __future__ import annotations

from typing import Final

# --- Span kind ---------------------------------------------------------------

SPAN_KIND: Final = "openinference.span.kind"

KIND_LLM: Final = "LLM"
KIND_CHAIN: Final = "CHAIN"
KIND_TOOL: Final = "TOOL"
KIND_RETRIEVER: Final = "RETRIEVER"
KIND_RERANKER: Final = "RERANKER"
KIND_EMBEDDING: Final = "EMBEDDING"
KIND_AGENT: Final = "AGENT"
KIND_GUARDRAIL: Final = "GUARDRAIL"
KIND_EVALUATOR: Final = "EVALUATOR"
KIND_UNKNOWN: Final = "UNKNOWN"

# --- Input / output ----------------------------------------------------------

INPUT_VALUE: Final = "input.value"
INPUT_MIME_TYPE: Final = "input.mime_type"
OUTPUT_VALUE: Final = "output.value"
OUTPUT_MIME_TYPE: Final = "output.mime_type"

MIME_JSON: Final = "application/json"
MIME_TEXT: Final = "text/plain"

# --- LLM ---------------------------------------------------------------------

LLM_MODEL_NAME: Final = "llm.model_name"
LLM_PROVIDER: Final = "llm.provider"
LLM_SYSTEM: Final = "llm.system"
LLM_INVOCATION_PARAMETERS: Final = "llm.invocation_parameters"
LLM_PROMPTS: Final = "llm.prompts"

LLM_TOKEN_COUNT_PROMPT: Final = "llm.token_count.prompt"
LLM_TOKEN_COUNT_COMPLETION: Final = "llm.token_count.completion"
LLM_TOKEN_COUNT_TOTAL: Final = "llm.token_count.total"
LLM_TOKEN_COUNT_PROMPT_CACHE_HIT: Final = "llm.token_count.prompt_details.cache_read"
LLM_TOKEN_COUNT_PROMPT_CACHE_WRITE: Final = "llm.token_count.prompt_details.cache_write"
LLM_TOKEN_COUNT_COMPLETION_REASONING: Final = "llm.token_count.completion_details.reasoning"

LLM_COST_PROMPT: Final = "llm.cost.prompt"
LLM_COST_COMPLETION: Final = "llm.cost.completion"
LLM_COST_TOTAL: Final = "llm.cost.total"

LLM_TOOLS: Final = "llm.tools"

# --- Flattened message paths -------------------------------------------------

LLM_INPUT_MESSAGES: Final = "llm.input_messages"
LLM_OUTPUT_MESSAGES: Final = "llm.output_messages"

MESSAGE_ROLE: Final = "message.role"
MESSAGE_CONTENT: Final = "message.content"
MESSAGE_TOOL_CALLS: Final = "message.tool_calls"

TOOL_CALL_ID: Final = "tool_call.id"
TOOL_CALL_FUNCTION_NAME: Final = "tool_call.function.name"
TOOL_CALL_FUNCTION_ARGUMENTS: Final = "tool_call.function.arguments"

# --- Embedding ---------------------------------------------------------------

EMBEDDING_MODEL_NAME: Final = "embedding.model_name"

# --- Tool --------------------------------------------------------------------

TOOL_NAME: Final = "tool.name"
TOOL_DESCRIPTION: Final = "tool.description"
TOOL_PARAMETERS: Final = "tool.parameters"

# --- Retrieval ---------------------------------------------------------------

RETRIEVAL_DOCUMENTS: Final = "retrieval.documents"
DOCUMENT_ID: Final = "document.id"
DOCUMENT_CONTENT: Final = "document.content"
DOCUMENT_SCORE: Final = "document.score"
DOCUMENT_METADATA: Final = "document.metadata"

# --- Session / user ----------------------------------------------------------

SESSION_ID: Final = "session.id"
USER_ID: Final = "user.id"
TAG_TAGS: Final = "tag.tags"
METADATA: Final = "metadata"

# --- Prompt management -------------------------------------------------------

PROMPT_TEMPLATE_TEMPLATE: Final = "llm.prompt_template.template"
PROMPT_TEMPLATE_VERSION: Final = "llm.prompt_template.version"


def message_key(base: str, index: int, suffix: str) -> str:
    """Build a flattened message attribute key.

    >>> message_key(LLM_INPUT_MESSAGES, 0, MESSAGE_ROLE)
    'llm.input_messages.0.message.role'
    """
    return f"{base}.{index}.{suffix}"


def document_key(index: int, suffix: str) -> str:
    """Build a flattened retrieved-document attribute key."""
    return f"{RETRIEVAL_DOCUMENTS}.{index}.{suffix}"
