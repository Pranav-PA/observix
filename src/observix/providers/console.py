"""Console destination --- print spans to stdout.

Useful for local development and for seeing exactly what a dialect produces
before pointing at a real backend. Defaults to the ``passthrough`` dialect so
you see canonical attributes; set ``dialect="langfuse"`` to preview what
Langfuse would receive.
"""

from __future__ import annotations

import sys
from typing import ClassVar

from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SpanExporter

from ..config import ExporterConfig
from .base import Provider, ProviderContext


class ConsoleProvider(Provider):
    """Write spans to a stream as JSON."""

    name: ClassVar[str] = "console"
    default_dialect: ClassVar[str] = "passthrough"
    endpoint_env: ClassVar[str | None] = None

    def create_exporter(self, config: ExporterConfig, context: ProviderContext) -> SpanExporter:
        stream_name = str(config.options.get("stream", "stdout")).lower()
        stream = sys.stderr if stream_name == "stderr" else sys.stdout
        return ConsoleSpanExporter(out=stream)
