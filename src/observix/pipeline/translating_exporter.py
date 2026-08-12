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
    span: ReadableSpan,
    *,
    attributes: dict[str, Any],
    name: str | None = None,
    resource: Any = None,
) -> ReadableSpan:
    """Clone ``span`` with replacement attributes, name and resource."""
    candidate: dict[str, Any] = {
        "name": name or span.name,
        "context": span.get_span_context(),
        "parent": span.parent,
        "resource": resource if resource is not None else span.resource,
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
        resource_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._exporter = exporter
        self._dialect = dialect
        self._redaction = redaction or ALLOW_ALL
        self._destination = destination
        self._adopt_foreign = adopt_foreign
        self._resource_overrides = resource_overrides or {}
        self._translation_failures = 0
        #: Resources are identical across a batch, so the merged result is
        #: computed once and reused rather than rebuilt per span.
        self._resource_cache: dict[int, Any] = {}

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
            return rebuild_span(
                span,
                attributes=result.attributes,
                name=result.name,
                resource=self._merged_resource(span),
            )
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

    def _merged_resource(self, span: ReadableSpan) -> Any:
        """This destination's resource: the shared one plus its own overrides."""
        if not self._resource_overrides:
            return None  # rebuild_span keeps the original
        source = span.resource
        key = id(source)
        cached = self._resource_cache.get(key)
        if cached is None:
            from opentelemetry.sdk.resources import Resource

            # Resource.create() injects defaults (service.name=unknown_service,
            # telemetry.sdk.*) which would then win the merge and clobber the
            # real values. The plain constructor adds nothing.
            cached = source.merge(Resource(dict(self._resource_overrides)))
            self._resource_cache[key] = cached
        return cached

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return bool(self._exporter.force_flush(timeout_millis))

    def __repr__(self) -> str:
        return (
            f"<DialectSpanExporter destination={self._destination!r} "
            f"dialect={self._dialect.name!r} exporter={type(self._exporter).__name__}>"
        )
