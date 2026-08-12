"""Shared fixtures.

The whole suite runs with ``OBSERVIX_STRICT=1`` so the fail-open guard re-raises
instead of swallowing. A bug that only shows up as a suppressed WARNING is a bug
that ships.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("OBSERVIX_STRICT", "1")

from observix import shutdown
from observix.cost.model import reset_prices
from observix.errors import reset_warned_sites


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    """Leave no global state between tests."""
    reset_warned_sites()
    yield
    shutdown()
    reset_prices()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove ambient configuration that would leak into tests."""
    for key in list(os.environ):
        if key.startswith("OBSERVIX_") and key != "OBSERVIX_STRICT":
            monkeypatch.delenv(key, raising=False)
    for key in (
        "OTEL_SERVICE_NAME",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "PHOENIX_COLLECTOR_ENDPOINT",
        "PHOENIX_API_KEY",
        "ARIZE_SPACE_ID",
        "ARIZE_API_KEY",
        "DD_API_KEY",
        "DD_SITE",
        "MLFLOW_TRACKING_URI",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def sample_messages() -> list:
    return [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "What is 2+2?"},
    ]
