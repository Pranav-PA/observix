"""Live verification against a real MLflow tracking server.

Same rationale as the Phoenix suite: an in-memory test proves observix emits
what it intended to, not that MLflow *understands* it. This suite sends real
spans over OTLP and reads them back through MLflow's own client, asserting on
the typed fields MLflow only populates for attributes it recognises.

It has already earned its keep --- it found that MLflow has a native
``mlflow.llm.cost`` attribute which observix was not emitting, so cost computed
from a custom price book was invisible in MLflow's cost reporting.

Run:
    pip install mlflow
    mlflow server --host 127.0.0.1 --port 5000     # separate terminal
    pytest tests/live -m live

Skipped automatically when no MLflow is reachable, so CI stays green without one.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from observix import configure, flush, get_current_span, observe, observe_block, shutdown
from observix.cost import ModelPrice, register_price

MLFLOW_BASE = os.environ.get("MLFLOW_BASE_URL", "http://127.0.0.1:5000")
POLL_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 2.0


def _mlflow_is_up() -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"{MLFLOW_BASE}/health", timeout=3) as response:
            return bool(200 <= response.status < 300)
    except Exception:
        return False


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _mlflow_is_up(),
        reason=f"No MLflow reachable at {MLFLOW_BASE}. Start one with: mlflow server",
    ),
]


@pytest.fixture(scope="module")
def sent_spans():
    """Send a representative trace to MLflow, then read it back."""
    import mlflow

    mlflow.set_tracking_uri(MLFLOW_BASE)
    experiment = mlflow.set_experiment(f"observix-live-{uuid.uuid4().hex[:8]}")

    # Priced only in our book, so MLflow cannot compute a cost of its own and
    # any cost that appears must be the one observix sent.
    register_price("observix-live-model", ModelPrice(input=1000.0, output=2000.0))

    configure(
        service_name="observix-mlflow-live",
        set_global_tracer_provider=False,
        exporters=[
            {
                "provider": "mlflow",
                "endpoint": MLFLOW_BASE,
                "options": {"experiment_id": experiment.experiment_id},
            }
        ],
    )

    @observe(kind="chat", name="live_chat")
    def chat() -> str:
        get_current_span().record_llm_call(
            provider="acme",
            request_model="observix-live-model",
            input_messages=[
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "What is the capital of France?"},
            ],
            output_messages=[{"role": "assistant", "content": "Paris."}],
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            temperature=0.7,
        ).set_session(user_id="live_user", session_id="live_session")
        return "Paris."

    @observe(kind="agent", name="live_agent")
    def agent() -> str:
        with observe_block("live_tool", kind="tool") as span:
            span.set_tool(name="lookup", arguments={"q": "france"}, result="ok")
        return chat()

    agent()
    flush(10_000)
    shutdown()

    spans = _await_spans(experiment.experiment_id, expected=3)
    return {span.name: span for span in spans}


def _await_spans(experiment_id: str, *, expected: int):
    """Poll MLflow until the spans are queryable, or time out."""
    import mlflow

    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            traces = mlflow.search_traces(
                locations=[experiment_id], max_results=10, return_type="list"
            )
        except Exception:
            traces = []
        if traces:
            spans = [s for trace in traces for s in trace.data.spans]
            if len(spans) >= expected:
                return spans
        time.sleep(POLL_INTERVAL_S)

    pytest.fail(
        f"MLflow did not report {expected} spans for experiment {experiment_id!r} "
        f"within {POLL_TIMEOUT_S:.0f}s."
    )


class TestMLflowAcceptsOurSpans:
    def test_the_whole_trace_arrives(self, sent_spans) -> None:
        assert {"live_agent", "live_tool", "live_chat"} <= set(sent_spans)

    def test_parent_child_structure_survives(self, sent_spans) -> None:
        chat = sent_spans["live_chat"]
        agent = sent_spans["live_agent"]
        assert chat.parent_id == agent.span_id


class TestMLflowUnderstandsTheDialect:
    """MLflow only populates these typed fields for attributes it recognises."""

    def test_span_types_are_recognised(self, sent_spans) -> None:
        assert sent_spans["live_chat"].span_type == "CHAT_MODEL"
        assert sent_spans["live_tool"].span_type == "TOOL"
        assert sent_spans["live_agent"].span_type == "AGENT"

    def test_inputs_and_outputs_are_promoted(self, sent_spans) -> None:
        """MLflow parses spanInputs/spanOutputs into typed fields; if the JSON
        shape were wrong these would stay empty."""
        chat = sent_spans["live_chat"]
        assert chat.inputs, "MLflow did not populate span.inputs"
        assert chat.outputs, "MLflow did not populate span.outputs"
        assert "capital of France" in str(chat.inputs)
        assert "Paris" in str(chat.outputs)

    def test_token_usage_is_parsed_as_an_object(self, sent_spans) -> None:
        usage = sent_spans["live_chat"].attributes["mlflow.chat.tokenUsage"]
        assert usage["input_tokens"] == 1_000_000
        assert usage["output_tokens"] == 1_000_000
        assert usage["total_tokens"] == 2_000_000

    def test_model_and_provider_are_recognised(self, sent_spans) -> None:
        attrs = sent_spans["live_chat"].attributes
        assert attrs["mlflow.llm.model"] == "observix-live-model"
        assert attrs["mlflow.llm.provider"] == "acme"

    def test_cost_reaches_mlflows_native_field(self, sent_spans) -> None:
        """Regression: observix used to leave cost in its own namespace, so a
        model MLflow cannot price showed no cost at all in MLflow."""
        cost = sent_spans["live_chat"].attributes["mlflow.llm.cost"]
        assert cost["input_cost"] == pytest.approx(1000.0)
        assert cost["output_cost"] == pytest.approx(2000.0)
        assert cost["total_cost"] == pytest.approx(3000.0)

    def test_session_and_user_are_recognised(self, sent_spans) -> None:
        attrs = sent_spans["live_chat"].attributes
        assert attrs["mlflow.trace.session"] == "live_session"
        assert attrs["mlflow.trace.user"] == "live_user"


class TestNoForeignVocabularyLeaks:
    def test_no_canonical_namespace_reaches_mlflow(self, sent_spans) -> None:
        """observix.* must never reach a backend outside the passthrough dialect."""
        leaked = {
            name: [k for k in span.attributes if k.startswith("observix.")]
            for name, span in sent_spans.items()
        }
        leaked = {k: v for k, v in leaked.items() if v}
        assert not leaked, f"Canonical attributes leaked to MLflow: {leaked}"

    def test_no_other_backends_namespace_reaches_mlflow(self, sent_spans) -> None:
        for span in sent_spans.values():
            keys = " ".join(span.attributes)
            assert "langfuse." not in keys
            assert "openinference." not in keys
