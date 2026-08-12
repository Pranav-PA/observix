"""Test helpers.

Spans reach the collector *after* redaction and dialect translation, so a test
asserts on exactly the attributes a real backend would have received --- which
is the only way to catch a dialect regression before your users do.

    from observix.testing import collect_spans

    def test_llm_span():
        with collect_spans(dialect="openinference") as spans:
            my_instrumented_function()
        span = spans.one()
        assert span.attributes["llm.token_count.prompt"] == 42
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan

from .config import ExporterConfig, ObservixConfig
from .init import configure, flush, shutdown
from .providers.memory import InMemorySpanExporter


class SpanCollector:
    """Access to the spans captured during a :func:`collect_spans` block.

    Reading :attr:`spans` flushes first, so assertions work *inside* the
    ``with`` block as well as after it. Whether a span is still sitting in a
    batch queue is an implementation detail a test should never have to know.
    """

    def __init__(
        self, exporter: InMemorySpanExporter, flush_fn: Callable[[], Any] | None = None
    ) -> None:
        self._exporter = exporter
        self._flush = flush_fn

    @property
    def spans(self) -> list[ReadableSpan]:
        """Every captured span, in export order. Flushes pending exports first."""
        if self._flush is not None:
            self._flush()
        return self._exporter.get_finished_spans()

    def __len__(self) -> int:
        return len(self.spans)

    def __iter__(self) -> Iterator[ReadableSpan]:
        return iter(self.spans)

    def __getitem__(self, index: int) -> ReadableSpan:
        return self.spans[index]

    def one(self) -> ReadableSpan:
        """The single captured span. Raises if there is not exactly one."""
        spans = self.spans
        if len(spans) != 1:
            names = ", ".join(repr(s.name) for s in spans)
            raise AssertionError(f"Expected exactly 1 span, found {len(spans)}: [{names}]")
        return spans[0]

    def named(self, name: str) -> list[ReadableSpan]:
        """Every span with the given name."""
        return [s for s in self.spans if s.name == name]

    def first_named(self, name: str) -> ReadableSpan:
        """The first span with the given name."""
        matches = self.named(name)
        if not matches:
            available = ", ".join(sorted({repr(s.name) for s in self.spans}))
            raise AssertionError(f"No span named {name!r}. Captured: [{available}]")
        return matches[0]

    def attributes(self, name: str | None = None) -> dict[str, Any]:
        """Attributes of one span --- the only one, or the first so named."""
        span = self.first_named(name) if name is not None else self.one()
        return dict(span.attributes or {})

    def names(self) -> list[str]:
        """Names of every captured span, in order."""
        return [s.name for s in self.spans]

    def clear(self) -> None:
        self._exporter.clear()

    def __repr__(self) -> str:
        return f"<SpanCollector spans={len(self.spans)}>"


@contextmanager
def collect_spans(
    *,
    dialect: str = "passthrough",
    service_name: str = "test-service",
    redact: Any | None = None,
    capture_content: bool = True,
    sample_ratio: float = 1.0,
    exporters: Sequence[str | ExporterConfig | dict] | None = None,
    **configure_kwargs: Any,
) -> Iterator[SpanCollector]:
    """Configure observix to capture spans in memory for the duration.

    Args:
        dialect: Which dialect to translate through before capture. Use the one
            matching the backend whose rendering you are testing.
        redact: Redaction policy to apply, exactly as a real destination would.
        exporters: Replace the default single in-memory destination entirely,
            for tests that assert on fan-out to several destinations.

    Yields:
        A :class:`SpanCollector` over the captured spans.
    """
    memory = InMemorySpanExporter()

    if exporters is None:
        exporters = [
            ExporterConfig(
                provider="memory",
                name="memory",
                dialect=dialect,
                redact=redact,
                options={"exporter": memory},
            )
        ]

    configure(
        service_name=service_name,
        exporters=exporters,
        capture_content=capture_content,
        sample_ratio=sample_ratio,
        set_global_tracer_provider=False,
        **configure_kwargs,
    )
    try:
        yield SpanCollector(memory, flush_fn=lambda: flush(5_000))
    finally:
        flush(5_000)
        shutdown()


@contextmanager
def multi_collector(
    destinations: dict[str, str],
    *,
    service_name: str = "test-service",
    **configure_kwargs: Any,
) -> Iterator[dict[str, SpanCollector]]:
    """Capture the same spans at several destinations, each with its own dialect.

    Args:
        destinations: Mapping of destination name to dialect name, e.g.
            ``{"phoenix": "openinference", "langfuse": "langfuse"}``.

    Yields:
        A collector per destination, so a single run can be asserted against
        every backend's rendering at once.
    """
    exporters: list[ExporterConfig] = []
    collectors: dict[str, SpanCollector] = {}

    for name, dialect in destinations.items():
        memory = InMemorySpanExporter()
        collectors[name] = SpanCollector(memory, flush_fn=lambda: flush(5_000))
        exporters.append(
            ExporterConfig(
                provider="memory",
                name=name,
                dialect=dialect,
                options={"exporter": memory},
            )
        )

    configure(
        service_name=service_name,
        exporters=exporters,
        set_global_tracer_provider=False,
        **configure_kwargs,
    )
    try:
        yield collectors
    finally:
        flush(5_000)
        shutdown()


def build_test_config(**kwargs: Any) -> ObservixConfig:
    """Build a configuration wired to an in-memory destination."""
    kwargs.setdefault("service_name", "test-service")
    kwargs.setdefault("set_global_tracer_provider", False)
    kwargs.setdefault("exporters", [ExporterConfig(provider="memory")])
    return ObservixConfig(**kwargs)
