"""Langfuse destination.

Langfuse authenticates OTLP with HTTP Basic using the public/secret key pair,
exposes traces at ``/api/public/otel/v1/traces``, and does **not** accept gRPC.
Regional hosts are selected with ``region="us" | "eu" | "jp" | "hipaa"``.
"""

from __future__ import annotations

import base64
import os
from typing import ClassVar

from ..config import ExporterConfig
from ..errors import ConfigurationError
from .base import OTLPProviderBase

REGIONS: dict[str, str] = {
    "eu": "https://cloud.langfuse.com",
    "us": "https://us.cloud.langfuse.com",
    "jp": "https://jp.cloud.langfuse.com",
    "hipaa": "https://hipaa.cloud.langfuse.com",
}

DEFAULT_REGION = "eu"
OTEL_PATH = "/api/public/otel/v1/traces"


class LangfuseProvider(OTLPProviderBase):
    """Send ``langfuse.*``-shaped spans to Langfuse Cloud or self-hosted."""

    name: ClassVar[str] = "langfuse"
    default_dialect: ClassVar[str] = "langfuse"
    default_protocol: ClassVar[str] = "http/protobuf"
    endpoint_env: ClassVar[str | None] = "LANGFUSE_HOST"
    traces_path: ClassVar[str] = OTEL_PATH

    def resolve_endpoint(self, config: ExporterConfig) -> str | None:
        if config.endpoint:
            return _normalize(config.endpoint)

        host = os.environ.get("LANGFUSE_HOST")
        if host:
            return _normalize(host)

        region = str(
            config.options.get("region") or os.environ.get("LANGFUSE_REGION") or DEFAULT_REGION
        ).lower()
        base = REGIONS.get(region)
        if base is None:
            raise ConfigurationError(
                f"Unknown Langfuse region {region!r}. Expected one of: "
                f"{', '.join(sorted(REGIONS))}."
            )
        return base + OTEL_PATH

    def build_headers(self, config: ExporterConfig) -> dict[str, str]:
        public_key = config.options.get("public_key") or os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = config.options.get("secret_key") or os.environ.get("LANGFUSE_SECRET_KEY")

        headers: dict[str, str] = {}
        if public_key and secret_key:
            token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        elif not config.headers.get("Authorization"):
            raise ConfigurationError(
                "Langfuse requires credentials. Set LANGFUSE_PUBLIC_KEY and "
                "LANGFUSE_SECRET_KEY, or pass public_key/secret_key in the "
                "exporter's options."
            )

        # Opts into Langfuse's current ingestion semantics for OTLP spans.
        headers["x-langfuse-ingestion-version"] = str(config.options.get("ingestion_version", 4))
        headers.update(config.headers)
        return headers

    def create_exporter(self, config: ExporterConfig, context: object) -> object:  # type: ignore[override]
        protocol = (config.protocol or self.default_protocol).lower()
        if protocol in ("grpc", "otlp/grpc"):
            raise ConfigurationError(
                "Langfuse does not accept OTLP over gRPC. Use 'http/protobuf' or 'http/json'."
            )
        return super().create_exporter(config, context)  # type: ignore[arg-type]


def _normalize(endpoint: str) -> str:
    """Append Langfuse's OTLP path unless the URL already names a route."""
    stripped = endpoint.rstrip("/")
    if stripped.endswith(OTEL_PATH):
        return stripped
    if stripped.endswith("/api/public/otel"):
        return stripped + "/v1/traces"
    without_scheme = stripped.split("://", 1)[-1]
    if "/" in without_scheme:
        return stripped
    return stripped + OTEL_PATH
