"""Adoption of spans from *real* instrumentation libraries.

``observix.integrations.adopt`` was written from specifications. Specifications
describe what libraries are supposed to emit; these tests check what they
actually do emit, by running OpenLLMetry and OpenInference against a local HTTP
server that returns an OpenAI-shaped response. No network, no API key, but the
instrumentors run their full request/response path and produce genuine spans.

This found three real gaps on its first run:

* ``gen_ai.usage.total_tokens`` (OpenLLMetry) was never mapped.
* OpenInference hides every sampling parameter inside a
  ``llm.invocation_parameters`` JSON blob that was never unpacked, so adopting
  a Phoenix-instrumented span lost temperature and max_tokens entirely --- a
  Langfuse destination showed no model parameters at all.
* OpenInference emits ``llm.finish_reason`` (singular), which was never mapped.
"""

from __future__ import annotations

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from observix.dialects import CanonicalView, LangfuseDialect, OpenInferenceDialect
from observix.integrations.adopt import looks_foreign, normalize_foreign_attributes
from observix.model.enums import SpanKind
from observix.semconv import canonical as C

pytest.importorskip("openai", reason="pip install openai")

CHAT_RESPONSE: dict[str, Any] = {
    "id": "chatcmpl-test123",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4o-2024-08-06",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Paris is the capital of France."},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1200, "completion_tokens": 340, "total_tokens": 1540},
}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = json.dumps(CHAT_RESPONSE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def _capture_real_span(instrument):
    """Run a real instrumented OpenAI call and return the span it produced."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    instrumentor = instrument(provider)
    try:
        from openai import OpenAI

        OpenAI(api_key="sk-test", base_url=f"http://127.0.0.1:{port}/v1").chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "What is the capital of France?"},
            ],
            temperature=0.7,
            max_tokens=100,
        )
    finally:
        server.shutdown()
        # Best-effort teardown: leaving a live patch would leak into other tests.
        with contextlib.suppress(Exception):
            instrumentor.uninstrument()

    spans = exporter.get_finished_spans()
    assert spans, "the instrumentor produced no spans"
    return spans[0]


@pytest.fixture(scope="module")
def openllmetry_span():
    openllmetry = pytest.importorskip(
        "opentelemetry.instrumentation.openai",
        reason="pip install opentelemetry-instrumentation-openai",
    )

    def instrument(provider):
        inst = openllmetry.OpenAIInstrumentor()
        inst.instrument(tracer_provider=provider)
        return inst

    return _capture_real_span(instrument)


@pytest.fixture(scope="module")
def openinference_span():
    openinference = pytest.importorskip(
        "openinference.instrumentation.openai",
        reason="pip install openinference-instrumentation-openai",
    )

    def instrument(provider):
        inst = openinference.OpenAIInstrumentor()
        inst.instrument(tracer_provider=provider)
        return inst

    return _capture_real_span(instrument)


class TestOpenLLMetryAdoption:
    def test_the_span_is_recognised_as_foreign(self, openllmetry_span) -> None:
        assert looks_foreign(dict(openllmetry_span.attributes))

    def test_core_fields_are_adopted(self, openllmetry_span) -> None:
        adopted = normalize_foreign_attributes(dict(openllmetry_span.attributes))
        assert adopted[C.KIND] == SpanKind.CHAT.value
        assert adopted[C.LLM_PROVIDER] == "openai"
        assert adopted[C.LLM_REQUEST_MODEL] == "gpt-4o"
        assert adopted[C.LLM_RESPONSE_MODEL] == "gpt-4o-2024-08-06"
        assert adopted[C.USAGE_INPUT_TOKENS] == 1200
        assert adopted[C.USAGE_OUTPUT_TOKENS] == 340

    def test_total_tokens_are_adopted(self, openllmetry_span) -> None:
        """Regression: gen_ai.usage.total_tokens was never mapped."""
        adopted = normalize_foreign_attributes(dict(openllmetry_span.attributes))
        assert adopted[C.USAGE_TOTAL_TOKENS] == 1540

    def test_request_parameters_are_adopted(self, openllmetry_span) -> None:
        adopted = normalize_foreign_attributes(dict(openllmetry_span.attributes))
        assert adopted[C.LLM_REQUEST_TEMPERATURE] == 0.7
        assert adopted[C.LLM_REQUEST_MAX_TOKENS] == 100

    def test_content_survives_adoption(self, openllmetry_span) -> None:
        adopted = normalize_foreign_attributes(dict(openllmetry_span.attributes))
        view = CanonicalView(adopted, name=openllmetry_span.name)
        assert "capital of France" in (view.input_text() or "")
        assert "Paris" in (view.output_text() or "")


class TestOpenInferenceAdoption:
    def test_the_span_is_recognised_as_foreign(self, openinference_span) -> None:
        assert looks_foreign(dict(openinference_span.attributes))

    def test_core_fields_are_adopted(self, openinference_span) -> None:
        adopted = normalize_foreign_attributes(dict(openinference_span.attributes))
        assert adopted[C.KIND] == SpanKind.CHAT.value
        assert adopted[C.LLM_PROVIDER] == "openai"
        assert adopted[C.USAGE_INPUT_TOKENS] == 1200
        assert adopted[C.USAGE_OUTPUT_TOKENS] == 340
        assert adopted[C.USAGE_TOTAL_TOKENS] == 1540

    def test_request_parameters_are_unpacked_from_the_json_blob(self, openinference_span) -> None:
        """Regression: OpenInference buries every sampling parameter inside
        llm.invocation_parameters, which adoption used to ignore entirely."""
        adopted = normalize_foreign_attributes(dict(openinference_span.attributes))
        assert adopted[C.LLM_REQUEST_TEMPERATURE] == 0.7
        assert adopted[C.LLM_REQUEST_MAX_TOKENS] == 100

    def test_requested_and_returned_models_are_separated(self, openinference_span) -> None:
        """`llm.model_name` is what came back; the request lives in the blob."""
        adopted = normalize_foreign_attributes(dict(openinference_span.attributes))
        assert adopted[C.LLM_REQUEST_MODEL] == "gpt-4o"
        assert adopted[C.LLM_RESPONSE_MODEL] == "gpt-4o-2024-08-06"

    def test_singular_finish_reason_is_adopted(self, openinference_span) -> None:
        """OpenInference emits llm.finish_reason, not gen_ai's plural form."""
        adopted = normalize_foreign_attributes(dict(openinference_span.attributes))
        assert "stop" in str(adopted[C.LLM_RESPONSE_FINISH_REASONS])

    def test_flattened_messages_are_rebuilt(self, openinference_span) -> None:
        adopted = normalize_foreign_attributes(dict(openinference_span.attributes))
        messages = CanonicalView(adopted).input_messages
        assert [m.role for m in messages] == ["system", "user"]
        assert "capital of France" in messages[1].text()


class TestAdoptedSpansFanOut:
    """The point of adoption: foreign spans reach every backend natively."""

    @pytest.mark.parametrize("fixture", ["openllmetry_span", "openinference_span"])
    def test_adopted_spans_render_for_langfuse(self, fixture, request) -> None:
        span = request.getfixturevalue(fixture)
        adopted = normalize_foreign_attributes(dict(span.attributes))
        attrs = LangfuseDialect()(CanonicalView(adopted, name=span.name)).attributes

        assert attrs["langfuse.observation.type"] == "generation"
        assert "Paris" in attrs["langfuse.observation.output"]

        usage = json.loads(attrs["langfuse.observation.usage_details"])
        assert usage["input"] == 1200
        assert usage["output"] == 340

        params = json.loads(attrs["langfuse.observation.model.parameters"])
        assert params["temperature"] == 0.7
        assert params["max_tokens"] == 100

    @pytest.mark.parametrize("fixture", ["openllmetry_span", "openinference_span"])
    def test_adopted_spans_render_for_phoenix(self, fixture, request) -> None:
        span = request.getfixturevalue(fixture)
        adopted = normalize_foreign_attributes(dict(span.attributes))
        attrs = OpenInferenceDialect()(CanonicalView(adopted, name=span.name)).attributes

        assert attrs["openinference.span.kind"] == "LLM"
        assert attrs["llm.token_count.prompt"] == 1200
        assert attrs["llm.input_messages.0.message.role"] == "system"
        assert "capital of France" in attrs["llm.input_messages.1.message.content"]

    def test_a_langfuse_destination_gains_what_openllmetry_alone_could_not_give(
        self, openllmetry_span
    ) -> None:
        """Without adoption, Langfuse would have to infer from gen_ai.* --- the
        exact inference that leaves input/output null (langfuse#12657)."""
        raw = dict(openllmetry_span.attributes)
        adopted = normalize_foreign_attributes(raw)

        assert not any(k.startswith("langfuse.") for k in raw)
        attrs = LangfuseDialect()(CanonicalView(adopted)).attributes
        assert attrs["langfuse.observation.input"]
        assert attrs["langfuse.observation.output"]
