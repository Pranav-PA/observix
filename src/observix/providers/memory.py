"""In-memory destination --- collect spans for assertions.

The backbone of :mod:`observix.testing`. Spans arrive here *after* redaction
and dialect translation, so a test can assert on exactly the attributes a real
backend would have received.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import ClassVar

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from ..config import ExporterConfig
from .base import Provider, ProviderContext


class InMemorySpanExporter(SpanExporter):
    """Accumulate exported spans in a thread-safe list."""

    def __init__(self) -> None:
        self._spans: list[ReadableSpan] = []
        self._lock = threading.Lock()
        self._shutdown = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            if self._shutdown:
                return SpanExportResult.FAILURE
            self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def get_finished_spans(self) -> list[ReadableSpan]:
        """Snapshot of everything exported so far."""
        with self._lock:
            return list(self._spans)

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


class MemoryProvider(Provider):
    """Destination that keeps spans in process.

    Pass an existing :class:`InMemorySpanExporter` via the ``exporter`` option
    to control where spans land; otherwise one is created per destination.
    """

    name: ClassVar[str] = "memory"
    default_dialect: ClassVar[str] = "passthrough"
    endpoint_env: ClassVar[str | None] = None

    def create_exporter(self, config: ExporterConfig, context: ProviderContext) -> SpanExporter:
        existing = config.options.get("exporter")
        if isinstance(existing, InMemorySpanExporter):
            return existing
        return InMemorySpanExporter()
