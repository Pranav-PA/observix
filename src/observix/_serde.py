"""Defensive serialisation helpers.

Telemetry frequently has to record objects that were never designed to be
serialised -- ORM instances, numpy arrays, vendor SDK response models. Nothing
in here is allowed to raise: an unserialisable value degrades to a ``repr``,
never to an exception in the user's call path.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
from typing import Any

#: Values longer than this are truncated during fallback ``repr`` encoding, so a
#: single pathological object cannot produce a multi-megabyte attribute.
_MAX_REPR = 4096


def _fallback(value: Any) -> Any:
    """Coerce an otherwise unserialisable object into something JSON-safe."""
    # Dataclass instances (not classes).
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return dataclasses.asdict(value)
        except Exception:
            pass

    # Pydantic v2, then v1.
    for attr in ("model_dump", "dict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                return method()
            except Exception:
                pass

    # numpy / torch / anything with .tolist()
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return tolist()
        except Exception:
            pass

    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"

    text = repr(value)
    return text if len(text) <= _MAX_REPR else text[:_MAX_REPR] + "...<truncated>"


def to_json(value: Any) -> str:
    """Serialise ``value`` to a JSON string, never raising.

    Returns a JSON-encoded ``repr`` as a last resort.
    """
    try:
        return json.dumps(value, default=_fallback, ensure_ascii=False)
    except Exception:
        try:
            return json.dumps(_fallback(value), ensure_ascii=False)
        except Exception:
            return '"<unserializable>"'


def from_json(text: str) -> Any | None:
    """Parse a JSON string, returning ``None`` on any failure."""
    try:
        return json.loads(text)
    except Exception:
        return None


def as_text(value: Any) -> str:
    """Render ``value`` as a flat string for backends that want scalar content.

    Plain strings pass through untouched; everything else is JSON-encoded.
    """
    if isinstance(value, str):
        return value
    return to_json(value)
