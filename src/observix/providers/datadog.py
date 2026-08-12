"""Datadog destination.

Two shapes are supported:

* **Agent** (default) --- OTLP into a local Datadog Agent's OTLP receiver.
* **Intake** --- OTLP straight to Datadog, authenticated with ``DD_API_KEY``,
  by setting ``site`` in the exporter's options.

Datadog reads OTel GenAI conventions, so ``otel_genai`` is the default dialect.
"""

from __future__ import annotations

import os
from typing import ClassVar

from ..config import ExporterConfig
from ..errors import ConfigurationError
from .base import OTLPProviderBase

DEFAULT_AGENT_ENDPOINT = "http://localhost:4318"


class DatadogProvider(OTLPProviderBase):
    """Send GenAI-shaped spans to Datadog."""

    name: ClassVar[str] = "datadog"
    default_dialect: ClassVar[str] = "otel_genai"
    endpoint_env: ClassVar[str | None] = "DD_OTLP_ENDPOINT"

    def resolve_endpoint(self, config: ExporterConfig) -> str | None:
        endpoint = super().resolve_endpoint(config)
        if endpoint:
            return endpoint

        site = config.options.get("site") or os.environ.get("DD_SITE")
        if site:
            from .base import _with_traces_path

            return _with_traces_path(f"https://trace.agent.{site}", self.traces_path)

        from .base import _with_traces_path

        return _with_traces_path(DEFAULT_AGENT_ENDPOINT, self.traces_path)

    def build_headers(self, config: ExporterConfig) -> dict[str, str]:
        headers: dict[str, str] = {}

        api_key = config.options.get("api_key") or os.environ.get("DD_API_KEY")
        site = config.options.get("site") or os.environ.get("DD_SITE")

        if site and not api_key and not config.headers:
            raise ConfigurationError(
                "Sending to the Datadog intake requires an API key. Set DD_API_KEY, "
                "or drop 'site' to export via a local Datadog Agent instead."
            )
        if api_key:
            headers["dd-api-key"] = str(api_key)

        headers.update(config.headers)
        return headers
