"""Backend destinations.

A provider knows one backend's endpoint, auth and preferred dialect, and
returns a standard OpenTelemetry exporter. Add your own by subclassing
:class:`Provider` and declaring an ``observix.providers`` entry point.
"""

from .arize import ArizeProvider
from .base import OTLPProviderBase, Provider, ProviderContext, make_otlp_http_exporter
from .console import ConsoleProvider
from .datadog import DatadogProvider
from .langfuse import LangfuseProvider
from .memory import InMemorySpanExporter, MemoryProvider
from .mlflow import MLflowProvider
from .otlp import OTLPProvider
from .phoenix import PhoenixProvider
from .registry import (
    available_providers,
    register_provider,
    resolve_provider,
    unregister_provider,
)

__all__ = [
    "ArizeProvider",
    "ConsoleProvider",
    "DatadogProvider",
    "InMemorySpanExporter",
    "LangfuseProvider",
    "MLflowProvider",
    "MemoryProvider",
    "OTLPProvider",
    "OTLPProviderBase",
    "PhoenixProvider",
    "Provider",
    "ProviderContext",
    "available_providers",
    "make_otlp_http_exporter",
    "register_provider",
    "resolve_provider",
    "unregister_provider",
]
