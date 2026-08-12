"""Langfuse OTLP attribute conventions.

Langfuse documents that attributes in the ``langfuse.*`` namespace take
**highest precedence** over its inference from ``gen_ai.*`` / OpenInference /
MLflow. Writing them explicitly is what avoids the well-known failure mode
where GenAI-semconv spans land with null input/output (langfuse#12657).
"""

from __future__ import annotations

from typing import Final

# --- Trace level -------------------------------------------------------------

TRACE_NAME: Final = "langfuse.trace.name"
TRACE_INPUT: Final = "langfuse.trace.input"
TRACE_OUTPUT: Final = "langfuse.trace.output"
TRACE_TAGS: Final = "langfuse.trace.tags"
TRACE_PUBLIC: Final = "langfuse.trace.public"
TRACE_METADATA_PREFIX: Final = "langfuse.trace.metadata."

USER_ID: Final = "langfuse.user.id"
SESSION_ID: Final = "langfuse.session.id"
RELEASE: Final = "langfuse.release"
VERSION: Final = "langfuse.version"
ENVIRONMENT: Final = "langfuse.environment"

# --- Observation level -------------------------------------------------------

OBSERVATION_TYPE: Final = "langfuse.observation.type"
OBSERVATION_LEVEL: Final = "langfuse.observation.level"
OBSERVATION_STATUS_MESSAGE: Final = "langfuse.observation.status_message"
OBSERVATION_INPUT: Final = "langfuse.observation.input"
OBSERVATION_OUTPUT: Final = "langfuse.observation.output"
OBSERVATION_METADATA_PREFIX: Final = "langfuse.observation.metadata."

OBSERVATION_MODEL_NAME: Final = "langfuse.observation.model.name"
OBSERVATION_MODEL_PARAMETERS: Final = "langfuse.observation.model.parameters"
OBSERVATION_USAGE_DETAILS: Final = "langfuse.observation.usage_details"
OBSERVATION_COST_DETAILS: Final = "langfuse.observation.cost_details"

OBSERVATION_PROMPT_NAME: Final = "langfuse.observation.prompt.name"
OBSERVATION_PROMPT_VERSION: Final = "langfuse.observation.prompt.version"

OBSERVATION_COMPLETION_START_TIME: Final = "langfuse.observation.completion_start_time"

# --- Observation types -------------------------------------------------------

TYPE_SPAN: Final = "span"
TYPE_GENERATION: Final = "generation"
TYPE_EVENT: Final = "event"
TYPE_AGENT: Final = "agent"
TYPE_TOOL: Final = "tool"
TYPE_CHAIN: Final = "chain"
TYPE_RETRIEVER: Final = "retriever"
TYPE_EMBEDDING: Final = "embedding"
TYPE_GUARDRAIL: Final = "guardrail"
TYPE_EVALUATOR: Final = "evaluator"

# --- Observation levels ------------------------------------------------------

LEVEL_DEBUG: Final = "DEBUG"
LEVEL_DEFAULT: Final = "DEFAULT"
LEVEL_WARNING: Final = "WARNING"
LEVEL_ERROR: Final = "ERROR"


def trace_metadata_key(name: str) -> str:
    return TRACE_METADATA_PREFIX + name


def observation_metadata_key(name: str) -> str:
    return OBSERVATION_METADATA_PREFIX + name
