"""OpenTelemetry GenAI semantic conventions.

Tracks ``open-telemetry/semantic-conventions-genai`` (split out of the main
semconv repo in v1.42.0, June 2026). That specification is still
Development-status, which is precisely why observix keeps its own canonical
namespace and confines these names to a single dialect.
"""

from __future__ import annotations

from typing import Final

# --- Operation ---------------------------------------------------------------

OPERATION_NAME: Final = "gen_ai.operation.name"
PROVIDER_NAME: Final = "gen_ai.provider.name"
CONVERSATION_ID: Final = "gen_ai.conversation.id"
AGENT_NAME: Final = "gen_ai.agent.name"

# Well-known gen_ai.operation.name values.
OP_CHAT: Final = "chat"
OP_GENERATE_CONTENT: Final = "generate_content"
OP_TEXT_COMPLETION: Final = "text_completion"
OP_EMBEDDINGS: Final = "embeddings"
OP_EXECUTE_TOOL: Final = "execute_tool"
OP_CREATE_AGENT: Final = "create_agent"
OP_INVOKE_AGENT: Final = "invoke_agent"
OP_RETRIEVAL: Final = "retrieval"

# --- Request -----------------------------------------------------------------

REQUEST_MODEL: Final = "gen_ai.request.model"
REQUEST_TEMPERATURE: Final = "gen_ai.request.temperature"
REQUEST_TOP_P: Final = "gen_ai.request.top_p"
REQUEST_TOP_K: Final = "gen_ai.request.top_k"
REQUEST_MAX_TOKENS: Final = "gen_ai.request.max_tokens"
REQUEST_STOP_SEQUENCES: Final = "gen_ai.request.stop_sequences"
REQUEST_FREQUENCY_PENALTY: Final = "gen_ai.request.frequency_penalty"
REQUEST_PRESENCE_PENALTY: Final = "gen_ai.request.presence_penalty"
REQUEST_SEED: Final = "gen_ai.request.seed"

# --- Response ----------------------------------------------------------------

RESPONSE_MODEL: Final = "gen_ai.response.model"
RESPONSE_ID: Final = "gen_ai.response.id"
RESPONSE_FINISH_REASONS: Final = "gen_ai.response.finish_reasons"
RESPONSE_TIME_TO_FIRST_CHUNK: Final = "gen_ai.response.time_to_first_chunk"

# --- Content (opt-in; may contain sensitive data) ----------------------------

INPUT_MESSAGES: Final = "gen_ai.input.messages"
OUTPUT_MESSAGES: Final = "gen_ai.output.messages"
SYSTEM_INSTRUCTIONS: Final = "gen_ai.system_instructions"
TOOL_DEFINITIONS: Final = "gen_ai.tool.definitions"

#: Legacy flat content keys (pre-v1.37). Still what several backends read
#: most reliably, so the dialect can emit them alongside the structured form.
LEGACY_PROMPT: Final = "gen_ai.prompt"
LEGACY_COMPLETION: Final = "gen_ai.completion"

# --- Usage -------------------------------------------------------------------

USAGE_INPUT_TOKENS: Final = "gen_ai.usage.input_tokens"
USAGE_OUTPUT_TOKENS: Final = "gen_ai.usage.output_tokens"

USAGE_TOTAL_TOKENS: Final = "gen_ai.usage.total_tokens"
"""Not in the current spec, but emitted by OpenLLMetry and worth adopting ---
providers occasionally report a total that is not input + output."""
USAGE_CACHE_READ_INPUT_TOKENS: Final = "gen_ai.usage.cache_read.input_tokens"
USAGE_CACHE_CREATION_INPUT_TOKENS: Final = "gen_ai.usage.cache_creation.input_tokens"

# --- Tool --------------------------------------------------------------------

TOOL_NAME: Final = "gen_ai.tool.name"
TOOL_DESCRIPTION: Final = "gen_ai.tool.description"
TOOL_CALL_ID: Final = "gen_ai.tool.call.id"
TOOL_CALL_ARGUMENTS: Final = "gen_ai.tool.call.arguments"
TOOL_CALL_RESULT: Final = "gen_ai.tool.call.result"
