"""observix --- provider-agnostic observability for Python and AI applications.

Instrument once with a vocabulary you own; decide where the telemetry goes with
configuration alone.

    from observix import observe, configure

    configure(service_name="my-app", exporters=["phoenix", "langfuse"])

    @observe
    def my_function():
        ...

Switching backends, adding a second one, or redacting prompts before they reach
a third party are all configuration changes. Application code does not move.

Built on OpenTelemetry: transport, batching, retries, sampling and context
propagation are OTel's, not ours. What observix adds is the canonical telemetry
model, per-backend dialect translation at export time, in-process fan-out to
several destinations, and per-destination privacy policy.
"""

from ._version import __version__
from .api import (
    attach_context,
    current_trace_id,
    extract_context,
    get_current_span,
    inject_context,
    observe,
    observe_block,
    start_span,
)
from .config import ExporterConfig, ObservixConfig
from .cost import ModelPrice, register_price, register_prices
from .dialects import Dialect, register_dialect, resolve_dialect
from .errors import (
    ConfigurationError,
    DialectNotFoundError,
    MissingDependencyError,
    ObservixError,
    ProviderNotFoundError,
)
from .init import configure, flush, get_config, get_pipelines, is_configured, shutdown
from .model import (
    Cost,
    Message,
    ObservixSpan,
    Part,
    RedactionMode,
    Role,
    SpanKind,
    TokenUsage,
)
from .providers import Provider, register_provider, resolve_provider
from .redaction import RedactionPolicy

__all__ = [
    "ConfigurationError",
    "Cost",
    "Dialect",
    "DialectNotFoundError",
    "ExporterConfig",
    "Message",
    "MissingDependencyError",
    "ModelPrice",
    # Configuration
    "ObservixConfig",
    # Errors
    "ObservixError",
    # Model
    "ObservixSpan",
    "Part",
    # Extensibility
    "Provider",
    "ProviderNotFoundError",
    "RedactionMode",
    "RedactionPolicy",
    "Role",
    "SpanKind",
    "TokenUsage",
    # Version
    "__version__",
    "attach_context",
    # Lifecycle
    "configure",
    "current_trace_id",
    "extract_context",
    "flush",
    "get_config",
    "get_current_span",
    "get_pipelines",
    # Propagation
    "inject_context",
    "is_configured",
    # Core API
    "observe",
    "observe_block",
    "register_dialect",
    "register_price",
    "register_prices",
    "register_provider",
    "resolve_dialect",
    "resolve_provider",
    "shutdown",
    "start_span",
]
