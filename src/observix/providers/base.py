"""Provider abstractions.

A **provider** knows how to reach one backend: its default endpoint, its
protocol, how to build auth headers from that vendor's environment variables,
and which dialect renders best there.

Providers deliberately do *no* transport work of their own --- they return a
standard OpenTelemetry ``SpanExporter``. Batching, retry and compression stay
where OpenTelemetry already implements them.
"""

from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field
from typing import Any, ClassVar

from opentelemetry.sdk.trace.export import SpanExporter

from ..config import ExporterConfig
from ..errors import ConfigurationError, MissingDependencyError


@dataclass
class ProviderContext:
    """Ambient information a provider may need when building an exporter."""

    service_name: str = "unknown_service"
    service_version: str | None = None
    environment: str | None = None
    resource_attributes: dict[str, Any] = field(default_factory=dict)


class Provider(abc.ABC):
    """Builds a :class:`SpanExporter` for one backend.

    Subclass, set :attr:`name` and :attr:`default_dialect`, implement
    :meth:`create_exporter`, and declare the class under the
    ``observix.providers`` entry-point group.
    """

    name: ClassVar[str] = ""

    default_dialect: ClassVar[str] = "otel_genai"
    """Dialect used when the destination does not override it."""

    default_protocol: ClassVar[str] = "http/protobuf"

    #: Environment variable holding this backend's endpoint, if it has one.
    endpoint_env: ClassVar[str | None] = None

    #: Path appended to a base URL that does not already name the traces route.
    traces_path: ClassVar[str] = "/v1/traces"

    @abc.abstractmethod
    def create_exporter(self, config: ExporterConfig, context: ProviderContext) -> SpanExporter:
        """Construct the exporter for this destination."""

    # --- Helpers available to subclasses ------------------------------------

    def resolve_endpoint(self, config: ExporterConfig) -> str | None:
        """Endpoint from explicit config, then this backend's env var."""
        if config.endpoint:
            return _with_traces_path(config.endpoint, self.traces_path)
        if self.endpoint_env:
            value = os.environ.get(self.endpoint_env)
            if value:
                return _with_traces_path(value, self.traces_path)
        return None

    def build_headers(self, config: ExporterConfig) -> dict[str, str]:
        """Headers for this destination. Subclasses add auth, then call super."""
        return dict(config.headers)

    def require_endpoint(self, config: ExporterConfig) -> str:
        """Resolve the endpoint or fail with an actionable message."""
        endpoint = self.resolve_endpoint(config)
        if not endpoint:
            hint = f" Set {self.endpoint_env}, or" if self.endpoint_env else ""
            raise ConfigurationError(
                f"Provider {self.name!r} needs an endpoint.{hint} pass "
                f"endpoint=... in the exporter configuration."
            )
        return endpoint

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


def _with_traces_path(endpoint: str, traces_path: str) -> str:
    """Append the traces route to a base URL that lacks one.

    A URL already ending in the route, or any URL carrying a path, is left
    alone --- users who spell out a full endpoint mean it.
    """
    stripped = endpoint.rstrip("/")
    if not traces_path:
        return stripped
    if stripped.endswith(traces_path):
        return stripped
    # Detect an existing path component after the authority.
    without_scheme = stripped.split("://", 1)[-1]
    if "/" in without_scheme:
        return stripped
    return stripped + traces_path


def make_otlp_http_exporter(
    *,
    endpoint: str,
    headers: dict[str, str],
    timeout: float | None = None,
    provider_name: str = "otlp",
    extra: dict[str, Any] | None = None,
) -> SpanExporter:
    """Build an OTLP/HTTP span exporter, with an actionable import error."""
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
        raise MissingDependencyError(
            provider_name, "opentelemetry-exporter-otlp-proto-http", "otlp"
        ) from exc

    kwargs: dict[str, Any] = {"endpoint": endpoint, "headers": headers}
    if timeout is not None:
        kwargs["timeout"] = int(timeout)
    if extra:
        kwargs.update(extra)
    return OTLPSpanExporter(**kwargs)


def make_otlp_grpc_exporter(
    *,
    endpoint: str,
    headers: dict[str, str],
    timeout: float | None = None,
    provider_name: str = "otlp",
    extra: dict[str, Any] | None = None,
) -> SpanExporter:
    """Build an OTLP/gRPC span exporter, with an actionable import error."""
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GRPCSpanExporter,
        )
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
        raise MissingDependencyError(
            provider_name, "opentelemetry-exporter-otlp-proto-grpc", "grpc"
        ) from exc

    kwargs: dict[str, Any] = {"endpoint": endpoint, "headers": tuple(headers.items())}
    if timeout is not None:
        kwargs["timeout"] = int(timeout)
    if extra:
        kwargs.update(extra)
    # The gRPC exporter package ships no stubs, so the constructor types as Any.
    exporter: SpanExporter = GRPCSpanExporter(**kwargs)
    return exporter


class OTLPProviderBase(Provider):
    """Shared implementation for the many OTLP-speaking backends."""

    def create_exporter(self, config: ExporterConfig, context: ProviderContext) -> SpanExporter:
        endpoint = self.require_endpoint(config)
        headers = self.build_headers(config)
        protocol = (config.protocol or self.default_protocol).lower()

        if protocol in ("grpc", "otlp/grpc"):
            return make_otlp_grpc_exporter(
                endpoint=endpoint,
                headers=headers,
                timeout=config.timeout,
                provider_name=self.name,
                extra=config.options.get("exporter_kwargs"),
            )
        return make_otlp_http_exporter(
            endpoint=endpoint,
            headers=headers,
            timeout=config.timeout,
            provider_name=self.name,
            extra=config.options.get("exporter_kwargs"),
        )
