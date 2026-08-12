"""MLflow tracing attribute conventions."""

from __future__ import annotations

from typing import Final

SPAN_TYPE: Final = "mlflow.spanType"
SPAN_INPUTS: Final = "mlflow.spanInputs"
SPAN_OUTPUTS: Final = "mlflow.spanOutputs"
SPAN_FUNCTION_NAME: Final = "mlflow.spanFunctionName"

LLM_MODEL: Final = "mlflow.llm.model"
LLM_PROVIDER: Final = "mlflow.llm.provider"

CHAT_USAGE: Final = "mlflow.chat.tokenUsage"
"""JSON object: ``{input_tokens, output_tokens, total_tokens}``."""

TRACE_SESSION: Final = "mlflow.trace.session"
TRACE_USER: Final = "mlflow.trace.user"

# --- Span types --------------------------------------------------------------

TYPE_LLM: Final = "LLM"
TYPE_CHAT_MODEL: Final = "CHAT_MODEL"
TYPE_EMBEDDING: Final = "EMBEDDING"
TYPE_TOOL: Final = "TOOL"
TYPE_AGENT: Final = "AGENT"
TYPE_CHAIN: Final = "CHAIN"
TYPE_RETRIEVER: Final = "RETRIEVER"
TYPE_RERANKER: Final = "RERANKER"
TYPE_PARSER: Final = "PARSER"
TYPE_UNKNOWN: Final = "UNKNOWN"
