"""Provider resolution."""

from __future__ import annotations

from typing import Any

from .._registry import Registry
from ..errors import ProviderNotFoundError
from .arize import ArizeProvider
from .base import Provider
from .console import ConsoleProvider
from .datadog import DatadogProvider
from .langfuse import LangfuseProvider
from .memory import MemoryProvider
from .mlflow import MLflowProvider
from .otlp import OTLPProvider
from .phoenix import PhoenixProvider

ProviderFactory = Provider | type[Provider]

_registry: Registry[ProviderFactory] = Registry("observix.providers", "provider")

_BUILTINS: dict[str, type[Provider]] = {
    "console": ConsoleProvider,
    "memory": MemoryProvider,
    "otlp": OTLPProvider,
    "phoenix": PhoenixProvider,
    "langfuse": LangfuseProvider,
    "mlflow": MLflowProvider,
    "arize": ArizeProvider,
    "datadog": DatadogProvider,
}

for _name, _cls in _BUILTINS.items():
    _registry.register(_name, _cls, override=False)


def register_provider(name: str, provider: ProviderFactory) -> None:
    """Register a provider under ``name``, overriding any existing one."""
    _registry.register(name, provider)


def unregister_provider(name: str) -> None:
    """Remove a provider registration. Used by tests."""
    _registry.unregister(name)


def available_providers() -> list[str]:
    """Names of every resolvable provider."""
    return _registry.names()


def resolve_provider(spec: str | Provider | type[Provider], **kwargs: Any) -> Provider:
    """Resolve ``spec`` to a :class:`Provider` instance.

    Raises:
        ProviderNotFoundError: if a name cannot be resolved.
    """
    if isinstance(spec, Provider):
        return spec
    if isinstance(spec, type) and issubclass(spec, Provider):
        return spec(**kwargs)

    if not isinstance(spec, str):
        raise ProviderNotFoundError(str(spec), available_providers())

    found = _registry.get(spec)
    if found is None:
        raise ProviderNotFoundError(spec, available_providers())
    if isinstance(found, Provider):
        return found
    return found(**kwargs)
