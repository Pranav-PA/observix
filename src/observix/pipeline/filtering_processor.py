"""Per-destination span filtering and sampling.

OpenTelemetry samples once, per ``TracerProvider``. That is the right model for
a single backend and the wrong one when you want everything in your own
Langfuse but 5% in a metered SaaS. This processor adds the missing per-exporter
decision without a second TracerProvider or a Collector.

Ratio sampling reuses the trace-id hash that ``TraceIdRatioBased`` uses, so a
destination configured at 0.25 keeps *whole traces* rather than a scatter of
disconnected spans, and two destinations at the same ratio agree on which
traces they keep.
"""

from __future__ import annotations

from collections.abc import Callable

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

from ..errors import logger, strict_mode

#: Same bound TraceIdRatioBased uses: the upper 64 bits of the trace id.
_MAX_TRACE_ID = 0xFFFFFFFFFFFFFFFF

SpanPredicate = Callable[[ReadableSpan], bool]


def trace_id_ratio_keeps(trace_id: int, ratio: float) -> bool:
    """Whether a trace id falls inside ``ratio``.

    Deterministic in the trace id, so every span of a trace decides alike.
    """
    if ratio >= 1.0:
        return True
    if ratio <= 0.0:
        return False
    return (trace_id & _MAX_TRACE_ID) < round(ratio * (_MAX_TRACE_ID + 1))


class FilteringSpanProcessor(SpanProcessor):
    """Gate spans reaching one destination's processor.

    Args:
        processor: The downstream processor, normally a ``BatchSpanProcessor``.
        sample_ratio: Fraction of *traces* to forward. ``1.0`` forwards all.
        predicate: Extra per-span test; a span is forwarded only if it returns
            ``True``. Exceptions are treated as ``True`` (fail open).
    """

    def __init__(
        self,
        processor: SpanProcessor,
        *,
        sample_ratio: float = 1.0,
        predicate: SpanPredicate | None = None,
    ) -> None:
        self._processor = processor
        self._ratio = max(0.0, min(1.0, float(sample_ratio)))
        self._predicate = predicate

    @property
    def wrapped_processor(self) -> SpanProcessor:
        return self._processor

    @property
    def sample_ratio(self) -> float:
        return self._ratio

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        # Forwarded unconditionally: the keep/drop decision is made at end, when
        # attributes the predicate may need are actually populated.
        self._processor.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        if not self._should_export(span):
            return
        self._processor.on_end(span)

    def _should_export(self, span: ReadableSpan) -> bool:
        if self._ratio < 1.0:
            context = span.get_span_context()
            trace_id = context.trace_id if context else 0
            if not trace_id_ratio_keeps(trace_id, self._ratio):
                return False

        if self._predicate is not None:
            try:
                return bool(self._predicate(span))
            except Exception:
                if strict_mode():
                    raise
                logger.warning(
                    "observix: span predicate raised; forwarding the span anyway",
                    exc_info=True,
                )
                return True  # fail open: a broken predicate must not lose telemetry
        return True

    def shutdown(self) -> None:
        self._processor.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return bool(self._processor.force_flush(timeout_millis))

    def __repr__(self) -> str:
        return (
            f"<FilteringSpanProcessor ratio={self._ratio} "
            f"processor={type(self._processor).__name__}>"
        )
