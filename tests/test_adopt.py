"""Adopting spans emitted by other instrumentation libraries."""

from __future__ import annotations

import json

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanContext, TraceFlags

from observix.dialects import LangfuseDialect, PassthroughDialect
from observix.integrations.adopt import (
    infer_kind,
    looks_foreign,
    normalize_foreign_attributes,
)
from observix.model.enums import SpanKind
from observix.pipeline import DialectSpanExporter
from observix.semconv import canonical as C
from observix.semconv import genai as G
from observix.semconv import langfuse as LF
from observix.semconv import mlflow as MF
from observix.semconv import openinference as OI


class TestForeignDetection:
    def test_recognises_openllmetry_spans(self) -> None:
        assert looks_foreign({G.OPERATION_NAME: "chat", G.REQUEST_MODEL: "gpt-4o"})

    def test_recognises_openinference_spans(self) -> None:
        assert looks_foreign({OI.SPAN_KIND: "LLM", OI.INPUT_VALUE: "hi"})

    def test_recognises_mlflow_spans(self) -> None:
        assert looks_foreign({MF.SPAN_TYPE: "LLM"})

    def test_our_own_spans_are_not_foreign(self) -> None:
        assert not looks_foreign({C.KIND: "chat", C.LLM_REQUEST_MODEL: "m"})

    def test_a_mixed_span_is_left_alone(self) -> None:
        """A span we already own must not be re-derived from its own output."""
        assert not looks_foreign({C.KIND: "chat", G.REQUEST_MODEL: "m"})

    def test_plain_http_spans_are_not_foreign(self) -> None:
        assert not looks_foreign({"http.method": "GET", "http.status_code": 200})


class TestKindInference:
    def test_from_openinference(self) -> None:
        assert infer_kind({OI.SPAN_KIND: "RETRIEVER"}) is SpanKind.RETRIEVER

    def test_from_gen_ai_operation(self) -> None:
        assert infer_kind({G.OPERATION_NAME: "execute_tool"}) is SpanKind.TOOL
        assert infer_kind({G.OPERATION_NAME: "invoke_agent"}) is SpanKind.AGENT

    def test_from_mlflow_span_type(self) -> None:
        assert infer_kind({MF.SPAN_TYPE: "CHAT_MODEL"}) is SpanKind.CHAT

    def test_token_counts_alone_imply_a_model_call(self) -> None:
        assert infer_kind({G.USAGE_INPUT_TOKENS: 10}) is SpanKind.CHAT

    def test_unknowable_kind_returns_none(self) -> None:
        assert infer_kind({"http.method": "GET"}) is None


class TestNormalisation:
    def test_maps_gen_ai_to_canonical(self) -> None:
        result = normalize_foreign_attributes(
            {
                G.OPERATION_NAME: "chat",
                G.REQUEST_MODEL: "gpt-4o",
                G.PROVIDER_NAME: "openai",
                G.USAGE_INPUT_TOKENS: 100,
                G.USAGE_OUTPUT_TOKENS: 20,
            }
        )
        assert result[C.KIND] == SpanKind.CHAT.value
        assert result[C.LLM_REQUEST_MODEL] == "gpt-4o"
        assert result[C.LLM_PROVIDER] == "openai"
        assert result[C.USAGE_INPUT_TOKENS] == 100

    def test_maps_the_pre_1_36_gen_ai_system_key(self) -> None:
        result = normalize_foreign_attributes(
            {G.OPERATION_NAME: "chat", "gen_ai.system": "anthropic"}
        )
        assert result[C.LLM_PROVIDER] == "anthropic"

    def test_maps_openinference_to_canonical(self) -> None:
        result = normalize_foreign_attributes(
            {
                OI.SPAN_KIND: "LLM",
                OI.LLM_MODEL_NAME: "claude-opus-4",
                OI.LLM_TOKEN_COUNT_PROMPT: 500,
                OI.INPUT_VALUE: "hello",
            }
        )
        assert result[C.KIND] == SpanKind.CHAT.value
        assert result[C.LLM_REQUEST_MODEL] == "claude-opus-4"
        assert result[C.USAGE_INPUT_TOKENS] == 500
        assert result[C.INPUT] == "hello"

    def test_rebuilds_messages_from_openinference_indexed_keys(self) -> None:
        result = normalize_foreign_attributes(
            {
                OI.SPAN_KIND: "LLM",
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": "Be brief.",
                "llm.input_messages.1.message.role": "user",
                "llm.input_messages.1.message.content": "Hello",
            }
        )
        messages = json.loads(result[C.INPUT_MESSAGES])
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[1]["parts"][0]["content"] == "Hello"

    def test_unpacks_mlflow_usage_json(self) -> None:
        result = normalize_foreign_attributes(
            {
                MF.SPAN_TYPE: "LLM",
                MF.CHAT_USAGE: json.dumps(
                    {"input_tokens": 30, "output_tokens": 5, "total_tokens": 35}
                ),
            }
        )
        assert result[C.USAGE_INPUT_TOKENS] == 30
        assert result[C.USAGE_TOTAL_TOKENS] == 35

    def test_foreign_attributes_are_preserved(self) -> None:
        """A destination that already understood them must keep working."""
        result = normalize_foreign_attributes({G.OPERATION_NAME: "chat", G.REQUEST_MODEL: "gpt-4o"})
        assert result[G.REQUEST_MODEL] == "gpt-4o"

    def test_never_overwrites_an_existing_canonical_value(self) -> None:
        source = {OI.SPAN_KIND: "LLM", C.LLM_REQUEST_MODEL: "ours", OI.LLM_MODEL_NAME: "theirs"}
        assert normalize_foreign_attributes(source)[C.LLM_REQUEST_MODEL] == "ours"

    def test_non_foreign_input_is_returned_unchanged(self) -> None:
        source = {C.KIND: "chat"}
        assert normalize_foreign_attributes(source) is source


def _foreign_span(**attributes: object) -> ReadableSpan:
    return ReadableSpan(
        name="foreign",
        context=SpanContext(1, 2, is_remote=False, trace_flags=TraceFlags(1)),
        attributes=attributes,
    )


class _Capture:
    def __init__(self) -> None:
        self.spans: list = []

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult

        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None: ...

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


class TestAdoptionInThePipeline:
    def test_an_openllmetry_span_becomes_renderable_in_langfuse(self) -> None:
        """The point of adoption: no re-instrumentation, still fans out."""
        inner = _Capture()
        exporter = DialectSpanExporter(inner, LangfuseDialect(), adopt_foreign=True)
        exporter.export(
            [
                _foreign_span(
                    **{
                        G.OPERATION_NAME: "chat",
                        G.REQUEST_MODEL: "gpt-4o",
                        G.USAGE_INPUT_TOKENS: 100,
                        G.LEGACY_PROMPT: "hello",
                    }
                )
            ]
        )
        attrs = inner.spans[0].attributes
        assert attrs[LF.OBSERVATION_TYPE] == LF.TYPE_GENERATION
        assert attrs[LF.OBSERVATION_MODEL_NAME] == "gpt-4o"
        assert attrs[LF.OBSERVATION_INPUT] == "hello"

    def test_adoption_is_off_by_default(self) -> None:
        inner = _Capture()
        DialectSpanExporter(inner, LangfuseDialect()).export(
            [_foreign_span(**{G.OPERATION_NAME: "chat", G.REQUEST_MODEL: "gpt-4o"})]
        )
        # Without adoption the span is not recognised as a generation.
        assert inner.spans[0].attributes.get(LF.OBSERVATION_MODEL_NAME) is None

    def test_redaction_applies_to_adopted_content(self) -> None:
        """Adopted prompts must obey the destination's privacy policy."""
        from observix.redaction import DROP_CONTENT

        inner = _Capture()
        exporter = DialectSpanExporter(
            inner, PassthroughDialect(), redaction=DROP_CONTENT, adopt_foreign=True
        )
        exporter.export(
            [
                _foreign_span(
                    **{OI.SPAN_KIND: "LLM", OI.INPUT_VALUE: "secret", OI.LLM_TOKEN_COUNT_PROMPT: 7}
                )
            ]
        )
        attrs = inner.spans[0].attributes
        assert C.INPUT not in attrs
        assert attrs[C.USAGE_INPUT_TOKENS] == 7
