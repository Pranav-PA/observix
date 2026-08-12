"""Live verification against a real Arize Phoenix instance.

Everything in the main suite asserts against an in-memory exporter, which
verifies that observix produces the attributes it *intends* to. It cannot
verify that Phoenix actually *accepts and understands* them --- and that is the
whole claim of the OpenInference dialect.

These tests send real spans over OTLP to a running Phoenix and read them back
through Phoenix's own client, so a mapping that Phoenix silently ignores fails
here rather than in someone's production trace view.

Run:
    pip install arize-phoenix
    phoenix serve                       # separate terminal
    pytest tests/live -m live

Skipped automatically when no Phoenix is reachable, so CI stays green without
one.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from observix import configure, flush, get_current_span, observe, observe_block, shutdown

pytestmark = pytest.mark.live

PHOENIX_BASE = os.environ.get("PHOENIX_BASE_URL", "http://localhost:6006")
POLL_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 1.0


def _phoenix_is_up() -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"{PHOENIX_BASE}/healthz", timeout=3) as response:
            return bool(200 <= response.status < 300)
    except Exception:
        return False


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _phoenix_is_up(),
        reason=f"No Phoenix reachable at {PHOENIX_BASE}. Start one with: phoenix serve",
    ),
]


@pytest.fixture(scope="module")
def project_name() -> str:
    """A unique project per run, so repeated runs never see stale spans."""
    return f"observix-live-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def sent_spans(project_name: str):
    """Send a representative trace to Phoenix, then read it back."""
    configure(
        service_name="observix-live-test",
        set_global_tracer_provider=False,
        exporters=[
            {
                "provider": "phoenix",
                "endpoint": PHOENIX_BASE,
                "options": {"project_name": project_name},
            }
        ],
    )

    @observe(kind="chat", name="live_chat")
    def chat() -> str:
        get_current_span().record_llm_call(
            provider="anthropic",
            request_model="claude-opus-4",
            input_messages=[
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "What is the capital of France?"},
            ],
            output_messages=[{"role": "assistant", "content": "Paris."}],
            system_instructions="Be concise.",
            input_tokens=1200,
            output_tokens=340,
            temperature=0.7,
            max_tokens=1024,
            finish_reasons=["stop"],
        ).set_session(user_id="live_user", session_id="live_session")
        return "Paris."

    @observe(kind="agent", name="live_agent")
    def agent() -> str:
        with observe_block("live_retrieval", kind="retriever") as span:
            span.set_retrieval(
                query="capital of France",
                documents=[
                    {"id": "d1", "content": "Paris is the capital.", "score": 0.98},
                    {"id": "d2", "content": "France is in Europe.", "score": 0.71},
                ],
                top_k=2,
            )
        with observe_block("live_tool", kind="tool") as span:
            span.set_tool(name="lookup", arguments={"q": "france"}, result="ok")
        return chat()

    agent()
    flush(10_000)
    shutdown()

    spans = _await_spans(project_name, expected=4)
    return spans


def _await_spans(project_name: str, *, expected: int):
    """Poll Phoenix until the spans are queryable, or time out."""
    from phoenix.client import Client

    client = Client(base_url=PHOENIX_BASE)
    deadline = time.monotonic() + POLL_TIMEOUT_S

    while time.monotonic() < deadline:
        try:
            frame = client.spans.get_spans_dataframe(project_identifier=project_name)
        except Exception:
            frame = None
        if frame is not None and len(frame) >= expected:
            return frame
        time.sleep(POLL_INTERVAL_S)

    pytest.fail(
        f"Phoenix did not report {expected} spans for project {project_name!r} "
        f"within {POLL_TIMEOUT_S:.0f}s."
    )


def _row(frame, name: str):
    """The single row for a span name, as a plain dict of non-null values."""
    matches = frame[frame["name"] == name]
    assert len(matches) >= 1, (
        f"Phoenix has no span named {name!r}. Present: {sorted(set(frame['name']))}"
    )
    record = matches.iloc[0].to_dict()
    return {k: v for k, v in record.items() if v is not None and str(v) != "nan"}


class TestPhoenixAcceptsOurSpans:
    def test_the_whole_trace_arrives(self, sent_spans) -> None:
        names = set(sent_spans["name"])
        assert {"live_agent", "live_retrieval", "live_tool", "live_chat"} <= names

    def test_parent_child_structure_survives(self, sent_spans) -> None:
        chat = _row(sent_spans, "live_chat")
        agent = _row(sent_spans, "live_agent")
        assert chat.get("parent_id") == agent.get("context.span_id")

    def test_one_trace_id_for_the_whole_tree(self, sent_spans) -> None:
        assert sent_spans["context.trace_id"].nunique() == 1


class TestPhoenixUnderstandsOpenInference:
    """Phoenix promotes recognised OpenInference attributes into typed columns.

    A mapping Phoenix does not understand stays buried in raw attributes and
    these assertions fail --- which is exactly the regression the in-memory
    suite cannot catch.
    """

    def test_span_kind_is_recognised(self, sent_spans) -> None:
        assert _row(sent_spans, "live_chat")["span_kind"] == "LLM"
        assert _row(sent_spans, "live_retrieval")["span_kind"] == "RETRIEVER"
        assert _row(sent_spans, "live_tool")["span_kind"] == "TOOL"

    def test_token_counts_are_recognised(self, sent_spans) -> None:
        chat = _row(sent_spans, "live_chat")
        assert int(chat["attributes.llm.token_count.prompt"]) == 1200
        assert int(chat["attributes.llm.token_count.completion"]) == 340
        assert int(chat["attributes.llm.token_count.total"]) == 1540

    def test_model_name_is_recognised(self, sent_spans) -> None:
        chat = _row(sent_spans, "live_chat")
        assert chat["attributes.llm.model_name"] == "claude-opus-4"

    def test_messages_are_structured_not_a_blob(self, sent_spans) -> None:
        """The reason the dialect flattens into indexed keys at all."""
        chat = _row(sent_spans, "live_chat")
        messages = chat["attributes.llm.input_messages"]
        assert len(messages) == 2
        roles = [m.get("message.role") or m["message"]["role"] for m in messages]
        assert roles == ["system", "user"]

    def test_output_messages_are_structured(self, sent_spans) -> None:
        chat = _row(sent_spans, "live_chat")
        messages = chat["attributes.llm.output_messages"]
        assert len(messages) == 1

    def test_input_output_values_are_populated(self, sent_spans) -> None:
        chat = _row(sent_spans, "live_chat")
        assert chat["attributes.input.value"]
        assert chat["attributes.output.value"]

    def test_json_content_is_labelled_json(self, sent_spans) -> None:
        """Mislabelling costs the reader Phoenix's structured viewer."""
        chat = _row(sent_spans, "live_chat")
        assert chat["attributes.input.mime_type"] == "application/json"

    def test_invocation_parameters_are_recognised(self, sent_spans) -> None:
        import json

        chat = _row(sent_spans, "live_chat")
        params = json.loads(chat["attributes.llm.invocation_parameters"])
        assert params["temperature"] == 0.7
        assert params["max_tokens"] == 1024

    def test_retrieved_documents_are_structured(self, sent_spans) -> None:
        retrieval = _row(sent_spans, "live_retrieval")
        documents = retrieval["attributes.retrieval.documents"]
        assert len(documents) == 2

    def test_tool_name_is_recognised(self, sent_spans) -> None:
        assert _row(sent_spans, "live_tool")["attributes.tool.name"] == "lookup"

    def test_cost_is_computed_and_accepted(self, sent_spans) -> None:
        chat = _row(sent_spans, "live_chat")
        assert float(chat["attributes.llm.cost.total"]) > 0

    def test_session_and_user_are_recognised(self, sent_spans) -> None:
        chat = _row(sent_spans, "live_chat")
        assert chat["attributes.session.id"] == "live_session"
        assert chat["attributes.user.id"] == "live_user"


class TestNoForeignVocabularyLeaks:
    def test_phoenix_receives_no_other_backends_namespace(self, sent_spans) -> None:
        columns = " ".join(sent_spans.columns)
        assert "langfuse." not in columns
        assert "mlflow." not in columns

    def test_phoenix_receives_no_canonical_namespace(self, sent_spans) -> None:
        """observix.* must never reach a backend outside the passthrough dialect."""
        leaked = [c for c in sent_spans.columns if "observix." in c]
        assert not leaked, f"Canonical attributes leaked to Phoenix: {leaked}"
