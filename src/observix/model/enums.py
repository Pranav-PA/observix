"""Enumerations that make up the canonical telemetry vocabulary."""

from __future__ import annotations

from enum import Enum


class SpanKind(str, Enum):
    """What an instrumented operation *is*, semantically.

    This is observix's own taxonomy. Every dialect maps it onto whatever the
    target backend understands (``openinference.span.kind``,
    ``gen_ai.operation.name``, ``langfuse.observation.type``, ...).
    """

    LLM = "llm"
    """A raw text-completion style model call."""

    CHAT = "chat"
    """A chat-completion style model call."""

    EMBEDDING = "embedding"
    """An embedding / vectorisation call."""

    TOOL = "tool"
    """Execution of a tool or function on the model's behalf."""

    AGENT = "agent"
    """An autonomous agent invocation."""

    WORKFLOW = "workflow"
    """A top-level, user-facing unit of work. Usually the trace root."""

    CHAIN = "chain"
    """A composed sequence of steps."""

    RETRIEVER = "retriever"
    """A document / context retrieval step."""

    RERANKER = "reranker"
    """A retrieval re-ranking step."""

    GUARDRAIL = "guardrail"
    """A safety, validation, or policy check."""

    TASK = "task"
    """A generic instrumented function. The default for ``@observe``."""

    UNKNOWN = "unknown"
    """Kind could not be determined."""

    @classmethod
    def coerce(cls, value: object) -> SpanKind:
        """Best-effort conversion from arbitrary user input.

        Unrecognised values fall back to :attr:`TASK` rather than raising --
        a typo in a ``kind=`` argument should not break the application.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value.strip().lower())
            except ValueError:
                return cls.TASK
        return cls.TASK

    def is_model_call(self) -> bool:
        """Whether this kind represents a call to a model."""
        return self in (SpanKind.LLM, SpanKind.CHAT, SpanKind.EMBEDDING)


class Role(str, Enum):
    """Canonical message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

    @classmethod
    def coerce(cls, value: object) -> str:
        """Normalise a role, preserving unknown roles as free-form strings."""
        if isinstance(value, cls):
            return value.value
        if isinstance(value, str):
            lowered = value.strip().lower()
            # Common aliases seen across vendor SDKs.
            if lowered in ("ai", "model", "bot"):
                return cls.ASSISTANT.value
            if lowered in ("human",):
                return cls.USER.value
            if lowered in ("function",):
                return cls.TOOL.value
            return lowered
        return cls.USER.value


class PartType(str, Enum):
    """Kinds of content inside a canonical message.

    Mirrors the structure of ``gen_ai.input.messages`` so the forward mapping
    is lossless.
    """

    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_CALL_RESPONSE = "tool_call_response"
    REASONING = "reasoning"
    BLOB = "blob"
    """Non-text content (image, audio, file) recorded by reference, not value."""


class RedactionMode(str, Enum):
    """How much recorded content a destination is allowed to receive."""

    ALL = "all"
    """Send everything. The default."""

    NONE = "none"
    """Drop prompts, completions, tool arguments/results and retrieved documents."""

    HASHED = "hashed"
    """Replace content with a stable SHA-256 prefix, preserving joinability."""

    TRUNCATED = "truncated"
    """Keep a bounded prefix of each content value."""

    @classmethod
    def coerce(cls, value: object) -> RedactionMode:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value.strip().lower())
            except ValueError as exc:
                valid = ", ".join(m.value for m in cls)
                raise ValueError(
                    f"Invalid redaction mode {value!r}. Expected one of: {valid}."
                ) from exc
        raise TypeError(f"Invalid redaction mode of type {type(value).__name__}.")
