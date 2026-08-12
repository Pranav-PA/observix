"""Canonical message representation.

The shape mirrors OpenTelemetry's ``gen_ai.input.messages`` structure --- a list
of ``{role, parts}`` objects --- because that is the richest of the vocabularies
we target. Down-converting to OpenInference's flattened indexed form
(``llm.input_messages.0.message.role``) or to Langfuse's plain ``input`` blob is
lossy in a controlled, predictable direction; going the other way would not be.

Normalisation accepts the message dialects real applications actually hold:
OpenAI/Anthropic-style dicts, plain strings, and already-canonical objects.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Union

from .enums import PartType, Role


@dataclass
class Part:
    """One piece of content inside a :class:`Message`."""

    type: str = PartType.TEXT.value
    content: Any = None
    #: Present for ``tool_call`` / ``tool_call_response`` parts.
    id: str | None = None
    #: Tool name, for ``tool_call`` parts.
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "content": self.content}
        if self.id is not None:
            out["id"] = self.id
        if self.name is not None:
            out["name"] = self.name
        return out

    @classmethod
    def text(cls, content: Any) -> Part:
        return cls(type=PartType.TEXT.value, content=content)


@dataclass
class Message:
    """A single turn in a conversation."""

    role: str = Role.USER.value
    parts: list[Part] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "parts": [p.to_dict() for p in self.parts]}

    def text(self) -> str:
        """Concatenate the textual parts. Used by flat-content dialects."""
        chunks: list[str] = []
        for part in self.parts:
            if part.type == PartType.TEXT.value and part.content is not None:
                chunks.append(part.content if isinstance(part.content, str) else str(part.content))
        return "\n".join(chunks)

    @classmethod
    def of(cls, role: str, content: Any) -> Message:
        return cls(role=Role.coerce(role), parts=[Part.text(content)])


MessageLike = Union["Message", dict[str, Any], str]


def _parts_from_content(content: Any) -> list[Part]:
    """Build parts from an OpenAI/Anthropic-style ``content`` field."""
    if content is None:
        return []
    if isinstance(content, str):
        return [Part.text(content)]
    if isinstance(content, dict):
        return [_part_from_dict(content)]
    if isinstance(content, (list, tuple)):
        parts: list[Part] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(_part_from_dict(item))
            elif isinstance(item, Part):
                parts.append(item)
            else:
                parts.append(Part.text(item))
        return parts
    return [Part.text(content)]


def _part_from_dict(raw: dict[str, Any]) -> Part:
    """Interpret one content block from a vendor SDK."""
    kind = str(raw.get("type") or PartType.TEXT.value).strip().lower()

    if kind in ("text", "input_text", "output_text"):
        return Part(type=PartType.TEXT.value, content=raw.get("text", raw.get("content")))

    if kind in ("tool_use", "tool_call", "function_call"):
        # OpenAI nests the payload under "function"; Anthropic keeps it flat.
        raw_fn = raw.get("function")
        fn: dict[str, Any] = raw_fn if isinstance(raw_fn, dict) else {}
        return Part(
            type=PartType.TOOL_CALL.value,
            content=raw.get("input", raw.get("arguments", fn.get("arguments"))),
            id=_opt_str(raw.get("id") or raw.get("tool_call_id")),
            name=_opt_str(raw.get("name") or fn.get("name")),
        )

    if kind in ("tool_result", "tool_call_response", "function_response"):
        return Part(
            type=PartType.TOOL_CALL_RESPONSE.value,
            content=raw.get("content", raw.get("output", raw.get("result"))),
            id=_opt_str(raw.get("tool_use_id") or raw.get("tool_call_id") or raw.get("id")),
            name=_opt_str(raw.get("name")),
        )

    if kind in ("thinking", "reasoning", "redacted_thinking"):
        return Part(
            type=PartType.REASONING.value,
            content=raw.get("thinking", raw.get("text", raw.get("content"))),
        )

    if kind in ("image", "image_url", "audio", "input_audio", "file", "document"):
        # Recorded by reference: embedding base64 payloads in a span is never right.
        return Part(type=PartType.BLOB.value, content={"media_type": kind})

    return Part(type=PartType.TEXT.value, content=raw.get("content", raw))


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _message_from_dict(raw: dict[str, Any]) -> Message:
    role = Role.coerce(raw.get("role", Role.USER.value))
    if "parts" in raw and isinstance(raw["parts"], (list, tuple)):
        parts = [p if isinstance(p, Part) else _part_from_dict(p) for p in raw["parts"]]
    else:
        parts = _parts_from_content(raw.get("content"))

    # OpenAI puts assistant tool calls in a sibling key, not in `content`.
    tool_calls = raw.get("tool_calls")
    if isinstance(tool_calls, (list, tuple)):
        for call in tool_calls:
            if isinstance(call, dict):
                parts.append(_part_from_dict({**call, "type": "tool_call"}))

    return Message(role=role, parts=parts)


def normalize_messages(messages: Any) -> list[Message]:
    """Convert user input into canonical messages. Never raises.

    Accepts a single message or a sequence, in any of the supported shapes.
    Unrecognised input degrades to a single user message wrapping the value.
    """
    if messages is None:
        return []
    if isinstance(messages, Message):
        return [messages]
    if isinstance(messages, (str, bytes)):
        return [Message.of(Role.USER.value, messages)]
    if isinstance(messages, dict):
        return [_message_from_dict(messages)]

    if isinstance(messages, Iterable):
        out: list[Message] = []
        for item in messages:
            if isinstance(item, Message):
                out.append(item)
            elif isinstance(item, dict):
                out.append(_message_from_dict(item))
            elif isinstance(item, str):
                out.append(Message.of(Role.USER.value, item))
            else:
                out.append(Message.of(Role.USER.value, item))
        return out

    return [Message.of(Role.USER.value, messages)]


def messages_to_dicts(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Serialise canonical messages for storage in a span attribute."""
    return [m.to_dict() for m in messages]


def messages_from_dicts(raw: Any) -> list[Message]:
    """Rehydrate canonical messages previously written by :func:`messages_to_dicts`."""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[Message] = []
    for item in raw:
        if isinstance(item, dict):
            parts_raw = item.get("parts")
            parts = (
                [
                    Part(
                        type=str(p.get("type", PartType.TEXT.value)),
                        content=p.get("content"),
                        id=_opt_str(p.get("id")),
                        name=_opt_str(p.get("name")),
                    )
                    for p in parts_raw
                    if isinstance(p, dict)
                ]
                if isinstance(parts_raw, (list, tuple))
                else []
            )
            out.append(Message(role=Role.coerce(item.get("role")), parts=parts))
    return out


def flatten_text(messages: Sequence[Message]) -> str:
    """Render a conversation as plain text, for backends that want a scalar."""
    return "\n\n".join(f"{m.role}: {m.text()}" for m in messages)
