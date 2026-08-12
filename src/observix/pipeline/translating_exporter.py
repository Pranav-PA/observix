"""The exporter that makes multi-backend fan-out work.

Wraps any :class:`~opentelemetry.sdk.trace.export.SpanExporter` and, for every
span passing through, applies this destination's redaction policy and dialect
before delegating.

Two implementation notes worth knowing:

* ``ReadableSpan.attributes`` is immutable, so translation cannot mutate in
  place. A *new* ``ReadableSpan`` is constructed carrying the translated
  attributes. It is a genuine ``ReadableSpan``, not a duck-typed proxy, so
  third-party exporters that type-check keep working.
* Constructor keywords are filtered against the running SDK's signature, so a
  newer or older opentelemetry-sdk does not break translation.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from ..dialects.base import CanonicalView, Dialect
from ..errors import logger, strict_mode
from ..redaction import ALLOW_ALL, RedactionPolicy


def _readable_span_kwargs() -> set[str]:
    """Constructor parameters supported by the installed ``ReadableSpan``."""
    try:
        params = inspect.signature(ReadableSpan.__init__).parameters
        # `instrumentation_info` is deprecated and warns when passed.
        return {n for n in params if n not in ("self", "instrumentation_info")}
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return {
            "name",
            "context",
            "parent",
            "resource",
            "attributes",
            "events",
            "links",
            "kind",
            "status",
            "start_time",
            "end_time",
            "instrumentation_scope",
        }


_SUPPORTED_KWARGS = _readable_span_kwargs()


def rebuild_span(
    span: ReadableSpan, *, attributes: dict[str, Any], name: str | None = None
) -> ReadableSpan:
    """Clone ``span`` with replacement attributes and an optional new name."""
    candidate: dict[str, Any] = {
        "name": name or span.name,
        "context": span.get_span_context(),
        "parent": span.parent,
        "resource": span.resource,
        "attributes": attributes,
        "events": span.events,
        "links": span.links,
        "kind": span.kind,
        "status": span.status,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "instrumentation_scope": span.instrumentation_scope,
    }
    return ReadableSpan(**{k: v for k, v in candidate.items() if k in _SUPPORTED_KWARGS})


class DialectSpanExporter(SpanExporter):
    """Translate spans into one backend's dialect, then delegate.

    Args:
        exporter: The real exporter for this destination.
        dialect: How to translate canonical attributes for this backend.
        redaction: What this destination may receive. Defaults to everything.
        destination: Name used in log messages.
    """

    def __init__(
        self,
        exporter: SpanExporter,
        dialect: Dialect,
        *,
        redaction: RedactionPolicy | None = None,
        destination: str = "unknown",
        adopt_foreign: bool = False,
    ) -> None:
        self._exporter = exporter
        self._dialect = dialect
        self._redaction = redaction or ALLOW_ALL
        self._destination = destination
        self._adopt_foreign = adopt_foreign
        self._translation_failures = 0

    @property
    def wrapped_exporter(self) -> SpanExporter:
        """The underlying exporter. Escape hatch for tests and introspection."""
        return self._exporter

    @property
    def dialect(self) -> Dialect:
        return self._dialect

    @property
    def redaction(self) -> RedactionPolicy:
        return self._redaction

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Translate then export. A span that fails translation is sent as-is."""
        translated = [self._translate(span) for span in spans]
        return self._exporter.export(translated)

    def _translate(self, span: ReadableSpan) -> ReadableSpan:
        try:
            source = dict(span.attributes or {})
            if self._adopt_foreign:
                # Map another library's vocabulary onto ours *before* redaction,
                # so policy applies to adopted content too.
                from ..integrations.adopt import normalize_foreign_attributes

                source = normalize_foreign_attributes(source)
            redacted = self._redaction.apply(source)
            view = CanonicalView(redacted, name=span.name)
            result = self._dialect(view)
            return rebuild_span(span, attributes=result.attributes, name=result.name)
        except Exception:
            if strict_mode():
                raise
            self._translation_failures += 1
            if self._translation_failures == 1:
                logger.warning(
                    "observix: dialect %r failed to translate a span for destination %r; "
                    "exporting untranslated (further failures log at DEBUG)",
                    self._dialect.name,
                    self._destination,
                    exc_info=True,
                )
            else:
                logger.debug(
                    "observix: translation failure #%d for destination %r",
                    self._translation_failures,
                    self._destination,
                    exc_info=True,
                )
            return span

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return bool(self._exporter.force_flush(timeout_millis))

    def __repr__(self) -> str:
        return (
            f"<DialectSpanExporter destination={self._destination!r} "
            f"dialect={self._dialect.name!r} exporter={type(self._exporter).__name__}>"
        )
