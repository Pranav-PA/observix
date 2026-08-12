"""Arize Phoenix destination.

Phoenix renders OpenInference natively, so that is the default dialect.
Defaults to a local Phoenix at ``http://localhost:6006``.
"""

from __future__ import annotations

import os
from typing import ClassVar

from ..config import ExporterConfig, parse_headers
from .base import OTLPProviderBase

DEFAULT_ENDPOINT = "http://localhost:6006"


class PhoenixProvider(OTLPProviderBase):
    """Send OpenInference-shaped spans to Phoenix."""

    name: ClassVar[str] = "phoenix"
    default_dialect: ClassVar[str] = "openinference"
    endpoint_env: ClassVar[str | None] = "PHOENIX_COLLECTOR_ENDPOINT"

    def resolve_endpoint(self, config: ExporterConfig) -> str | None:
        endpoint = super().resolve_endpoint(config)
        if endpoint:
            return endpoint
        from .base import _with_traces_path

        return _with_traces_path(DEFAULT_ENDPOINT, self.traces_path)

    def build_headers(self, config: ExporterConfig) -> dict[str, str]:
        headers: dict[str, str] = {}

        raw = os.environ.get("PHOENIX_CLIENT_HEADERS")
        if raw:
            headers.update(parse_headers(raw))

        api_key = config.options.get("api_key") or os.environ.get("PHOENIX_API_KEY")
        if api_key:
            headers.setdefault("authorization", f"Bearer {api_key}")

        project = config.options.get("project_name") or os.environ.get("PHOENIX_PROJECT_NAME")
        if project:
            headers.setdefault("x-phoenix-project-name", str(project))

        headers.update(config.headers)
        return headers
