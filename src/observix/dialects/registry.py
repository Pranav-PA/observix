"""Dialect resolution.

Entry points may supply either a :class:`~observix.dialects.base.Dialect`
instance or a zero-argument-constructible class; both are accepted.
"""

from __future__ import annotations

from typing import Any

from .._registry import Registry
from ..errors import DialectNotFoundError
from .base import Dialect
from .langfuse import LangfuseDialect
from .mlflow import MLflowDialect
from .openinference import OpenInferenceDialect
from .otel_genai import OTelGenAIDialect
from .passthrough import PassthroughDialect

DialectFactory = Dialect | type[Dialect]

_registry: Registry[DialectFactory] = Registry("observix.dialects", "dialect")

#: Registered eagerly so observix works from a source checkout, where entry
#: points may not be installed.
_BUILTINS: dict[str, type[Dialect]] = {
    "passthrough": PassthroughDialect,
    "otel_genai": OTelGenAIDialect,
    "openinference": OpenInferenceDialect,
    "langfuse": LangfuseDialect,
    "mlflow": MLflowDialect,
}

for _name, _cls in _BUILTINS.items():
    _registry.register(_name, _cls, override=False)


def register_dialect(name: str, dialect: DialectFactory) -> None:
    """Register a dialect under ``name``, overriding any existing one."""
    _registry.register(name, dialect)


def unregister_dialect(name: str) -> None:
    """Remove a dialect registration. Used by tests."""
    _registry.unregister(name)


def available_dialects() -> list[str]:
    """Names of every resolvable dialect."""
    return _registry.names()


def resolve_dialect(spec: str | Dialect | type[Dialect], **kwargs: Any) -> Dialect:
    """Resolve ``spec`` to a :class:`Dialect` instance.

    Accepts a registered name, a dialect class, or an already-built instance.
    ``kwargs`` are passed to the constructor when one is invoked.

    Raises:
        DialectNotFoundError: if a name cannot be resolved.
    """
    if isinstance(spec, Dialect):
        return spec
    if isinstance(spec, type) and issubclass(spec, Dialect):
        return spec(**kwargs)

    if not isinstance(spec, str):
        raise DialectNotFoundError(str(spec), available_dialects())

    found = _registry.get(spec)
    if found is None:
        raise DialectNotFoundError(spec, available_dialects())
    if isinstance(found, Dialect):
        return found
    return found(**kwargs)
