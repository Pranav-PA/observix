"""Per-destination content redaction.

Privacy is a property of the *destination*, not of the span. The same recorded
prompt can go in full to a self-hosted Langfuse, hashed to a shared staging
backend, and not at all to a third-party SaaS --- because redaction runs inside
each destination's exporter, after the span is recorded and before it is
translated.

Redaction runs on the *canonical* attributes, so a policy written once applies
identically no matter which dialect the destination uses.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from re import Pattern
from typing import Any

from .model.enums import RedactionMode
from .semconv import canonical as C

#: Conservative detectors. Deliberately not exhaustive --- these catch the
#: common accidental leaks; they are not a compliance control.
_PII_PATTERNS: dict[str, Pattern[str]] = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "phone": re.compile(r"\b(?:\+\d{1,3}[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b"),
    "ipv4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}

_REDACTED = "[redacted]"


@dataclass
class RedactionPolicy:
    """What a single destination is allowed to receive.

    Attributes:
        mode: How to treat recorded content (prompts, completions, documents,
            tool payloads).
        max_length: Character budget per content value under
            :attr:`~observix.model.enums.RedactionMode.TRUNCATED`, and an upper
            bound applied under :attr:`RedactionMode.ALL`.
        redact_keys: Regexes matched against *attribute names*. Any match is
            replaced wholesale, regardless of ``mode``.
        detect_pii: Apply the built-in PII detectors to content values.
        pii_types: Which detectors to run. ``None`` runs all of them.
        hash_salt: Mixed into hashes so identical content is not correlatable
            across destinations.
    """

    mode: RedactionMode = RedactionMode.ALL
    max_length: int | None = None
    redact_keys: Sequence[str] = field(default_factory=tuple)
    detect_pii: bool = False
    pii_types: Sequence[str] | None = None
    hash_salt: str = ""

    _compiled: list[Pattern[str]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.mode = RedactionMode.coerce(self.mode)
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self.redact_keys]

    @property
    def is_noop(self) -> bool:
        """Whether this policy would leave every attribute untouched."""
        return (
            self.mode is RedactionMode.ALL
            and self.max_length is None
            and not self._compiled
            and not self.detect_pii
        )

    @property
    def drops_content(self) -> bool:
        """Whether content is discarded outright."""
        return self.mode is RedactionMode.NONE

    def apply(self, attributes: Mapping[str, Any]) -> dict[str, Any]:
        """Return a redacted copy of ``attributes``."""
        if self.is_noop:
            return dict(attributes)

        out: dict[str, Any] = {}
        for key, value in attributes.items():
            if self._key_is_redacted(key):
                out[key] = _REDACTED
                continue
            if not C.is_content_key(key):
                out[key] = value
                continue
            redacted = self._redact_content(key, value)
            if redacted is not None:
                out[key] = redacted
        return out

    def _key_is_redacted(self, key: str) -> bool:
        return any(pattern.search(key) for pattern in self._compiled)

    def _redact_content(self, key: str, value: Any) -> Any | None:
        if self.mode is RedactionMode.NONE:
            return None

        if self.mode is RedactionMode.HASHED:
            return self._hash(value)

        text = value if isinstance(value, str) else None

        if self.detect_pii and text is not None:
            text = self._scrub_pii(text)

        limit = self.max_length
        if self.mode is RedactionMode.TRUNCATED and limit is None:
            limit = 512
        if limit is not None and text is not None and len(text) > limit:
            text = text[:limit] + f"...[truncated {len(text) - limit} chars]"

        return text if text is not None else value

    def _hash(self, value: Any) -> str:
        raw = value if isinstance(value, str) else repr(value)
        digest = hashlib.sha256((self.hash_salt + raw).encode("utf-8")).hexdigest()
        return f"sha256:{digest[:16]}"

    def _scrub_pii(self, text: str) -> str:
        names = self.pii_types if self.pii_types is not None else list(_PII_PATTERNS)
        for name in names:
            pattern = _PII_PATTERNS.get(name)
            if pattern is not None:
                text = pattern.sub(f"[{name}]", text)
        return text


#: The default: send everything, unmodified.
ALLOW_ALL = RedactionPolicy(mode=RedactionMode.ALL)

#: Drop all recorded content. Metrics, timings and model names still flow.
DROP_CONTENT = RedactionPolicy(mode=RedactionMode.NONE)


def coerce_policy(value: Any) -> RedactionPolicy:
    """Build a policy from a string, mapping, or existing policy."""
    if isinstance(value, RedactionPolicy):
        return value
    if value is None:
        return ALLOW_ALL
    if isinstance(value, str):
        return RedactionPolicy(mode=RedactionMode.coerce(value))
    if isinstance(value, Mapping):
        return RedactionPolicy(**dict(value))
    raise TypeError(f"Cannot build a RedactionPolicy from {type(value).__name__}.")
