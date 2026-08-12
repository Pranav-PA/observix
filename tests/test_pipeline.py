"""Redaction, per-destination sampling, and span rebuilding."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import ReadableSpan

from observix.model.enums import RedactionMode
from observix.pipeline import (
    DialectSpanExporter,
    FilteringSpanProcessor,
    rebuild_span,
    trace_id_ratio_keeps,
)
from observix.redaction import ALLOW_ALL, DROP_CONTENT, RedactionPolicy, coerce_policy
from observix.semconv import canonical as C


class TestRedactionPolicy:
    @pytest.fixture
    def attributes(self) -> dict:
        return {
            C.INPUT_MESSAGES: '[{"role":"user","parts":[{"content":"secret prompt"}]}]',
            C.OUTPUT: "the model said something",
            C.TOOL_ARGUMENTS: '{"api_key": "sk-123"}',
            C.USAGE_INPUT_TOKENS: 1200,
            C.LLM_REQUEST_MODEL: "claude-opus-4",
            C.SESSION_ID: "s_1",
        }

    def test_allow_all_is_a_noop(self, attributes: dict) -> None:
        assert ALLOW_ALL.is_noop
        assert ALLOW_ALL.apply(attributes) == attributes

    def test_none_drops_content_but_keeps_metrics(self, attributes: dict) -> None:
        result = DROP_CONTENT.apply(attributes)
        assert C.INPUT_MESSAGES not in result
        assert C.OUTPUT not in result
        assert C.TOOL_ARGUMENTS not in result
        # Metrics must survive: dashboards keep working at redact="none".
        assert result[C.USAGE_INPUT_TOKENS] == 1200
        assert result[C.LLM_REQUEST_MODEL] == "claude-opus-4"
        assert result[C.SESSION_ID] == "s_1"

    def test_hashed_is_unreadable_but_stable(self, attributes: dict) -> None:
        policy = RedactionPolicy(mode=RedactionMode.HASHED)
        first = policy.apply(attributes)
        second = policy.apply(attributes)
        assert first[C.OUTPUT].startswith("sha256:")
        assert "model said" not in first[C.OUTPUT]
        assert first[C.OUTPUT] == second[C.OUTPUT]  # joinable
        assert first[C.USAGE_INPUT_TOKENS] == 1200

    def test_salt_breaks_cross_destination_correlation(self, attributes: dict) -> None:
        a = RedactionPolicy(mode=RedactionMode.HASHED, hash_salt="a").apply(attributes)
        b = RedactionPolicy(mode=RedactionMode.HASHED, hash_salt="b").apply(attributes)
        assert a[C.OUTPUT] != b[C.OUTPUT]

    def test_truncated_keeps_a_bounded_prefix(self) -> None:
        policy = RedactionPolicy(mode=RedactionMode.TRUNCATED, max_length=10)
        result = policy.apply({C.OUTPUT: "x" * 100})
        assert result[C.OUTPUT].startswith("xxxxxxxxxx")
        assert "truncated 90 chars" in result[C.OUTPUT]

    def test_short_values_are_not_truncated(self) -> None:
        policy = RedactionPolicy(mode=RedactionMode.TRUNCATED, max_length=100)
        assert policy.apply({C.OUTPUT: "short"})[C.OUTPUT] == "short"

    def test_key_patterns_redact_regardless_of_mode(self) -> None:
        policy = RedactionPolicy(redact_keys=[r"session"])
        result = policy.apply({C.SESSION_ID: "s_1", C.USER_ID: "u_1"})
        assert result[C.SESSION_ID] == "[redacted]"
        assert result[C.USER_ID] == "u_1"

    def test_pii_detection_scrubs_content(self) -> None:
        policy = RedactionPolicy(detect_pii=True)
        result = policy.apply({C.OUTPUT: "reach me at alice@example.com"})
        assert "alice@example.com" not in result[C.OUTPUT]
        assert "[email]" in result[C.OUTPUT]

    def test_pii_detection_can_be_narrowed(self) -> None:
        policy = RedactionPolicy(detect_pii=True, pii_types=["ssn"])
        result = policy.apply({C.OUTPUT: "email alice@example.com ssn 123-45-6789"})
        assert "alice@example.com" in result[C.OUTPUT]
        assert "[ssn]" in result[C.OUTPUT]

    def test_pii_detection_does_not_touch_non_content_keys(self) -> None:
        policy = RedactionPolicy(detect_pii=True)
        result = policy.apply({C.USER_ID: "alice@example.com"})
        assert result[C.USER_ID] == "alice@example.com"

    def test_drops_content_reports_correctly(self) -> None:
        assert DROP_CONTENT.drops_content is True
        assert ALLOW_ALL.drops_content is False


class TestCoercePolicy:
    def test_from_string(self) -> None:
        assert coerce_policy("hashed").mode is RedactionMode.HASHED

    def test_from_mapping(self) -> None:
        policy = coerce_policy({"mode": "truncated", "max_length": 50})
        assert policy.mode is RedactionMode.TRUNCATED
        assert policy.max_length == 50

    def test_none_allows_everything(self) -> None:
        assert coerce_policy(None).is_noop

    def test_an_existing_policy_passes_through(self) -> None:
        policy = RedactionPolicy(mode=RedactionMode.NONE)
        assert coerce_policy(policy) is policy

    def test_an_invalid_mode_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid redaction mode"):
            coerce_policy("nonsense")


class TestTraceIdRatioSampling:
    def test_ratio_one_keeps_everything(self) -> None:
        assert all(trace_id_ratio_keeps(i, 1.0) for i in range(100))

    def test_ratio_zero_keeps_nothing(self) -> None:
        assert not any(trace_id_ratio_keeps(i, 0.0) for i in range(100))

    def test_the_decision_is_deterministic_in_the_trace_id(self) -> None:
        trace_id = 0x1234_5678_9ABC_DEF0_1234_5678_9ABC_DEF0
        assert trace_id_ratio_keeps(trace_id, 0.5) == trace_id_ratio_keeps(trace_id, 0.5)

    def test_the_ratio_is_approximately_honoured(self) -> None:
        import random

        rng = random.Random(42)
        ids = [rng.getrandbits(128) for _ in range(5000)]
        kept = sum(trace_id_ratio_keeps(i, 0.25) for i in ids)
        assert 0.20 < kept / len(ids) < 0.30

    def test_destinations_at_the_same_ratio_agree(self) -> None:
        """Two backends at 25% must keep the *same* traces, not different ones."""
        import random

        rng = random.Random(7)
        ids = [rng.getrandbits(128) for _ in range(500)]
        a = [trace_id_ratio_keeps(i, 0.3) for i in ids]
        b = [trace_id_ratio_keeps(i, 0.3) for i in ids]
        assert a == b


class _RecordingProcessor:
    """Minimal SpanProcessor that just counts what reaches it."""

    def __init__(self) -> None:
        self.ended: list = []
        self.started: list = []

    def on_start(self, span, parent_context=None) -> None:
        self.started.append(span)

    def on_end(self, span) -> None:
        self.ended.append(span)

    def shutdown(self) -> None: ...

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def _fake_span(trace_id: int = 1, name: str = "s", **attributes) -> ReadableSpan:
    from opentelemetry.trace import SpanContext, TraceFlags

    return ReadableSpan(
        name=name,
        context=SpanContext(trace_id, 2, is_remote=False, trace_flags=TraceFlags(1)),
        attributes=attributes,
    )


class TestFilteringSpanProcessor:
    def test_forwards_everything_at_ratio_one(self) -> None:
        downstream = _RecordingProcessor()
        processor = FilteringSpanProcessor(downstream, sample_ratio=1.0)
        for i in range(10):
            processor.on_end(_fake_span(trace_id=i + 1))
        assert len(downstream.ended) == 10

    def test_forwards_nothing_at_ratio_zero(self) -> None:
        downstream = _RecordingProcessor()
        processor = FilteringSpanProcessor(downstream, sample_ratio=0.0)
        for i in range(10):
            processor.on_end(_fake_span(trace_id=i + 1))
        assert downstream.ended == []

    def test_predicate_filters(self) -> None:
        downstream = _RecordingProcessor()
        processor = FilteringSpanProcessor(downstream, predicate=lambda s: s.name == "keep")
        processor.on_end(_fake_span(name="keep"))
        processor.on_end(_fake_span(name="drop"))
        assert [s.name for s in downstream.ended] == ["keep"]

    def test_on_start_is_always_forwarded(self) -> None:
        """The keep/drop decision waits for on_end, when attributes exist."""
        downstream = _RecordingProcessor()
        processor = FilteringSpanProcessor(downstream, sample_ratio=0.0)
        processor.on_start(_fake_span())
        assert len(downstream.started) == 1

    def test_ratio_is_clamped_to_the_valid_range(self) -> None:
        assert FilteringSpanProcessor(_RecordingProcessor(), sample_ratio=5.0).sample_ratio == 1.0
        assert FilteringSpanProcessor(_RecordingProcessor(), sample_ratio=-1).sample_ratio == 0.0


class _CapturingExporter:
    def __init__(self) -> None:
        self.spans: list = []

    def export(self, spans):
        self.spans.extend(spans)
        from opentelemetry.sdk.trace.export import SpanExportResult

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None: ...

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


class TestDialectSpanExporter:
    def test_applies_redaction_before_translation(self) -> None:
        from observix.dialects import PassthroughDialect

        inner = _CapturingExporter()
        exporter = DialectSpanExporter(
            inner,
            PassthroughDialect(),
            redaction=RedactionPolicy(mode=RedactionMode.NONE),
        )
        exporter.export([_fake_span(**{C.OUTPUT: "secret", C.USAGE_INPUT_TOKENS: 5})])

        attrs = inner.spans[0].attributes
        assert C.OUTPUT not in attrs
        assert attrs[C.USAGE_INPUT_TOKENS] == 5

    def test_applies_the_dialect(self) -> None:
        from observix.dialects import LangfuseDialect
        from observix.semconv import langfuse as LF

        inner = _CapturingExporter()
        exporter = DialectSpanExporter(inner, LangfuseDialect())
        exporter.export([_fake_span(**{C.KIND: "chat", C.LLM_REQUEST_MODEL: "m"})])

        assert inner.spans[0].attributes[LF.OBSERVATION_TYPE] == LF.TYPE_GENERATION

    def test_a_failing_dialect_exports_untranslated_rather_than_dropping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from observix.dialects.base import CanonicalView, Dialect, TranslationResult

        class BrokenDialect(Dialect):
            name = "broken"

            def translate(self, view: CanonicalView) -> TranslationResult:
                raise RuntimeError("dialect exploded")

        monkeypatch.delenv("OBSERVIX_STRICT", raising=False)
        inner = _CapturingExporter()
        exporter = DialectSpanExporter(inner, BrokenDialect())
        exporter.export([_fake_span(**{C.KIND: "chat"})])

        assert len(inner.spans) == 1  # never dropped
        assert inner.spans[0].attributes[C.KIND] == "chat"


class TestRebuildSpan:
    def test_produces_a_real_readable_span(self) -> None:
        rebuilt = rebuild_span(_fake_span(name="orig", a=1), attributes={"b": 2})
        assert isinstance(rebuilt, ReadableSpan)
        assert rebuilt.name == "orig"
        assert dict(rebuilt.attributes) == {"b": 2}

    def test_can_rename(self) -> None:
        rebuilt = rebuild_span(_fake_span(name="orig"), attributes={}, name="new")
        assert rebuilt.name == "new"

    def test_preserves_span_context(self) -> None:
        original = _fake_span(trace_id=0xABC)
        rebuilt = rebuild_span(original, attributes={})
        assert rebuilt.get_span_context().trace_id == 0xABC
