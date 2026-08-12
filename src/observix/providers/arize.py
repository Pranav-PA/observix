"""Arize AX destination.

Arize reads OpenInference and authenticates with a space id and API key.
"""

from __future__ import annotations

import os
from typing import ClassVar

from ..config import ExporterConfig
from ..errors import ConfigurationError
from .base import OTLPProviderBase

DEFAULT_ENDPOINT = "https://otlp.arize.com"


class ArizeProvider(OTLPProviderBase):
    """Send OpenInference-shaped spans to Arize AX."""

    name: ClassVar[str] = "arize"
    default_dialect: ClassVar[str] = "openinference"
    endpoint_env: ClassVar[str | None] = "ARIZE_COLLECTOR_ENDPOINT"

    def resolve_endpoint(self, config: ExporterConfig) -> str | None:
        endpoint = super().resolve_endpoint(config)
        if endpoint:
            return endpoint
        from .base import _with_traces_path

        return _with_traces_path(DEFAULT_ENDPOINT, self.traces_path)

    def build_headers(self, config: ExporterConfig) -> dict[str, str]:
        space_id = config.options.get("space_id") or os.environ.get("ARIZE_SPACE_ID")
        api_key = config.options.get("api_key") or os.environ.get("ARIZE_API_KEY")

        if not (space_id and api_key) and not config.headers:
            raise ConfigurationError(
                "Arize requires credentials. Set ARIZE_SPACE_ID and ARIZE_API_KEY, "
                "or pass space_id/api_key in the exporter's options."
            )

        headers: dict[str, str] = {}
        if space_id:
            headers["space_id"] = str(space_id)
        if api_key:
            headers["api_key"] = str(api_key)

        model_id = config.options.get("model_id") or os.environ.get("ARIZE_MODEL_ID")
        if model_id:
            headers["model_id"] = str(model_id)

        headers.update(config.headers)
        return headers
