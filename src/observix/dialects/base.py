"""Dialect abstractions.

A **dialect** is a pure function from the canonical model to one backend's
native attribute vocabulary. Purity is the point: dialects are trivially
unit-testable, have no I/O, and can be swapped per destination without
touching span creation.

Translation happens at *export* time rather than at span creation, which is
what allows a single recorded span to arrive natively-shaped at Phoenix,
Langfuse, MLflow and a generic OTLP backend simultaneously.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .._serde import from_json
from ..model.enums import SpanKind
from ..model.messages import Message, flatten_text, messages_from_dicts
from ..model.usage import Cost, TokenUsage
from ..semconv import canonical as C


class CanonicalView:
    """Read-only, typed view over a span's canonical attributes.

    Wraps a raw attribute mapping and decodes it lazily, so a dialect that
    never touches messages never pays to parse them.
    """

    __slots__ = ("_attrs", "_cache", "_name")

    def __init__(self, attributes: Mapping[str, Any], *, name: str = "") -> None:
        self._attrs = attributes
        self._name = name
        self._cache: dict[str, Any] = {}

    # --- Raw access ----------------------------------------------------------

    @property
    def attributes(self) -> Mapping[str, Any]:
        """All attributes, canonical and foreign."""
        return self._attrs

    @property
    def span_name(self) -> str:
        return self._name

    def get(self, key: str, default: Any = None) -> Any:
        return self._attrs.get(key, default)

    def passthrough_attributes(self) -> dict[str, Any]:
        """Attributes not owned by observix, which every dialect preserves.

        Anything a user set directly (``http.method``, custom keys) survives
        translation untouched.
        """
        return {
            key: value
            for key, value in self._attrs.items()
            if not key.startswith(C.NAMESPACE + ".")
        }

    # --- Core ----------------------------------------------------------------

    @property
    def kind(self) -> SpanKind:
        return SpanKind.coerce(self._attrs.get(C.KIND))

    @property
    def input(self) -> Any | None:
        return self._attrs.get(C.INPUT)

    @property
    def output(self) -> Any | None:
        return self._attrs.get(C.OUTPUT)

    @property
    def system_instructions(self) -> str | None:
        value = self._attrs.get(C.SYSTEM_INSTRUCTIONS)
        return None if value is None else str(value)

    # --- Messages ------------------------------------------------------------

    @property
    def input_messages(self) -> list[Message]:
        return self._decode_messages("input_messages", C.INPUT_MESSAGES)

    @property
    def output_messages(self) -> list[Message]:
        return self._decode_messages("output_messages", C.OUTPUT_MESSAGES)

    def _decode_messages(self, cache_key: str, attr: str) -> list[Message]:
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[no-any-return]
        raw = self._attrs.get(attr)
        messages: list[Message] = []
        if isinstance(raw, str):
            messages = messages_from_dicts(from_json(raw))
        elif isinstance(raw, (list, tuple)):
            messages = messages_from_dicts(raw)
        self._cache[cache_key] = messages
        return messages

    def resolved_input(self) -> Any | None:
        """Best available representation of input: messages, else raw input."""
        raw = self._attrs.get(C.INPUT_MESSAGES)
        if raw is not None:
            return raw
        return self._attrs.get(C.INPUT)

    def resolved_output(self) -> Any | None:
        """Best available representation of output: messages, else raw output."""
        raw = self._attrs.get(C.OUTPUT_MESSAGES)
        if raw is not None:
            return raw
        return self._attrs.get(C.OUTPUT)

    def input_text(self) -> str | None:
        """Input rendered as flat text, for backends that want a scalar."""
        messages = self.input_messages
        if messages:
            return flatten_text(messages)
        value = self._attrs.get(C.INPUT)
        return None if value is None else str(value)

    def output_text(self) -> str | None:
        """Output rendered as flat text, for backends that want a scalar."""
        messages = self.output_messages
        if messages:
            return "\n\n".join(m.text() for m in messages)
        value = self._attrs.get(C.OUTPUT)
        return None if value is None else str(value)

    # --- LLM -----------------------------------------------------------------

    @property
    def provider(self) -> str | None:
        value = self._attrs.get(C.LLM_PROVIDER)
        return None if value is None else str(value)

    @property
    def request_model(self) -> str | None:
        value = self._attrs.get(C.LLM_REQUEST_MODEL)
        return None if value is None else str(value)

    @property
    def response_model(self) -> str | None:
        value = self._attrs.get(C.LLM_RESPONSE_MODEL)
        return None if value is None else str(value)

    @property
    def model(self) -> str | None:
        """The most specific model identifier available."""
        return self.response_model or self.request_model

    @property
    def finish_reasons(self) -> Sequence[str] | None:
        value = self._attrs.get(C.LLM_RESPONSE_FINISH_REASONS)
        if value is None:
            return None
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value]

    @property
    def usage(self) -> TokenUsage:
        return TokenUsage(
            input_tokens=_opt_int(self._attrs.get(C.USAGE_INPUT_TOKENS)),
            output_tokens=_opt_int(self._attrs.get(C.USAGE_OUTPUT_TOKENS)),
            total_tokens=_opt_int(self._attrs.get(C.USAGE_TOTAL_TOKENS)),
            reasoning_tokens=_opt_int(self._attrs.get(C.USAGE_REASONING_TOKENS)),
            cache_read_input_tokens=_opt_int(self._attrs.get(C.USAGE_CACHE_READ_INPUT_TOKENS)),
            cache_write_input_tokens=_opt_int(self._attrs.get(C.USAGE_CACHE_WRITE_INPUT_TOKENS)),
        )

    @property
    def cost(self) -> Cost:
        return Cost(
            input_usd=_opt_float(self._attrs.get(C.COST_INPUT_USD)),
            output_usd=_opt_float(self._attrs.get(C.COST_OUTPUT_USD)),
            total_usd=_opt_float(self._attrs.get(C.COST_TOTAL_USD)),
        )

    # --- Identity ------------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        return _opt_str(self._attrs.get(C.SESSION_ID))

    @property
    def user_id(self) -> str | None:
        return _opt_str(self._attrs.get(C.USER_ID))

    @property
    def conversation_id(self) -> str | None:
        return _opt_str(self._attrs.get(C.CONVERSATION_ID))

    @property
    def tags(self) -> list[str] | None:
        value = self._attrs.get(C.TAGS)
        if value is None:
            return None
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value]

    @property
    def metadata(self) -> dict[str, Any]:
        """User metadata, with the ``observix.metadata.`` prefix stripped."""
        prefix_len = len(C.METADATA_PREFIX)
        return {
            key[prefix_len:]: value
            for key, value in self._attrs.items()
            if key.startswith(C.METADATA_PREFIX)
        }

    def __repr__(self) -> str:
        return f"<CanonicalView name={self._name!r} kind={self.kind.value}>"


@dataclass
class TranslationResult:
    """What a dialect produces: a possibly-renamed span with new attributes."""

    attributes: dict[str, Any] = field(default_factory=dict)
    name: str | None = None
    """Replacement span name. ``None`` keeps the original."""

    def set(self, key: str, value: Any) -> None:
        """Set an attribute, ignoring ``None`` so absent data stays absent."""
        if value is not None:
            self.attributes[key] = value

    def set_all(self, items: Mapping[str, Any]) -> None:
        for key, value in items.items():
            self.set(key, value)


class Dialect(abc.ABC):
    """Translates canonical telemetry into one backend's vocabulary.

    Subclass, set :attr:`name`, implement :meth:`translate`, and register the
    class under the ``observix.dialects`` entry-point group to make it
    available by name in configuration.
    """

    name: ClassVar[str] = ""

    #: Whether the dialect wants foreign (non-``observix.*``) attributes
    #: preserved in its output. Nearly always ``True``.
    preserve_foreign: ClassVar[bool] = True

    @abc.abstractmethod
    def translate(self, view: CanonicalView) -> TranslationResult:
        """Produce backend-native attributes from the canonical view."""

    def __call__(self, view: CanonicalView) -> TranslationResult:
        result = self.translate(view)
        if self.preserve_foreign:
            # Foreign attributes must not clobber what the dialect produced.
            for key, value in view.passthrough_attributes().items():
                result.attributes.setdefault(key, value)
        return result

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)
