"""MLflow tracing destination.

Targets an MLflow tracking server's OTLP ingest route. The experiment is
selected with the ``experiment_id`` option or ``MLFLOW_EXPERIMENT_ID``.
"""

from __future__ import annotations

import os
from typing import ClassVar

from ..config import ExporterConfig
from .base import OTLPProviderBase

DEFAULT_ENDPOINT = "http://localhost:5000"


class MLflowProvider(OTLPProviderBase):
    """Send ``mlflow.*``-shaped spans to an MLflow tracking server."""

    name: ClassVar[str] = "mlflow"
    default_dialect: ClassVar[str] = "mlflow"
    endpoint_env: ClassVar[str | None] = "MLFLOW_TRACKING_URI"
    traces_path: ClassVar[str] = "/v1/traces"

    def resolve_endpoint(self, config: ExporterConfig) -> str | None:
        endpoint = super().resolve_endpoint(config)
        if endpoint:
            return endpoint
        from .base import _with_traces_path

        return _with_traces_path(DEFAULT_ENDPOINT, self.traces_path)

    def build_headers(self, config: ExporterConfig) -> dict[str, str]:
        headers: dict[str, str] = {}

        experiment = config.options.get("experiment_id") or os.environ.get("MLFLOW_EXPERIMENT_ID")
        if experiment:
            headers["x-mlflow-experiment-id"] = str(experiment)

        token = config.options.get("token") or os.environ.get("MLFLOW_TRACKING_TOKEN")
        if token:
            headers.setdefault("Authorization", f"Bearer {token}")

        headers.update(config.headers)
        return headers
