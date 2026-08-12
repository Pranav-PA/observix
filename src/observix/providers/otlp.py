"""Generic OTLP destination.

Use this for any backend that speaks OTLP and has no dedicated provider:
Grafana Tempo, Jaeger, Honeycomb, SigNoz, New Relic, or your own Collector.
"""

from __future__ import annotations

from typing import ClassVar

from ..config import ExporterConfig
from .base import OTLPProviderBase


class OTLPProvider(OTLPProviderBase):
    """Standard OTLP exporter with GenAI semantic conventions."""

    name: ClassVar[str] = "otlp"
    default_dialect: ClassVar[str] = "otel_genai"
    endpoint_env: ClassVar[str | None] = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"

    def resolve_endpoint(self, config: ExporterConfig) -> str | None:
        endpoint = super().resolve_endpoint(config)
        if endpoint:
            return endpoint
        # Fall back to the generic OTLP endpoint variable, per the OTel spec.
        import os

        generic = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if generic:
            from .base import _with_traces_path

            return _with_traces_path(generic, self.traces_path)
        return None

    def build_headers(self, config: ExporterConfig) -> dict[str, str]:
        import os

        from ..config import parse_headers

        headers: dict[str, str] = {}
        for env in ("OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_TRACES_HEADERS"):
            raw = os.environ.get(env)
            if raw:
                headers.update(parse_headers(raw))
        headers.update(config.headers)
        return headers
