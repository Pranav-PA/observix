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

LLM_COST: Final = "mlflow.llm.cost"
"""JSON object: ``{input_cost, output_cost, total_cost}``, in USD.

MLflow populates this itself for models in its own price table, but only for
those. Emitting it explicitly is what makes cost visible for fine-tunes,
private models, and anything priced from a custom price book --- verified
against MLflow 3.x, where an unknown model produced no cost attribute at all.
"""

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
