"""End-to-end: the behaviour observix exists to provide.

Every test here exercises the full path --- record, filter, redact, translate,
export --- because that is the only place the product's central claim can be
verified: one instrumentation, many backends, each natively shaped.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from observix import (
    ExporterConfig,
    configure,
    flush,
    get_current_span,
    get_pipelines,
    is_configured,
    observe,
    observe_block,
    shutdown,
)
from observix.errors import ConfigurationError, ProviderNotFoundError
from observix.providers.memory import InMemorySpanExporter
from observix.semconv import canonical as C
from observix.semconv import genai as G
from observix.semconv import langfuse as LF
from observix.semconv import mlflow as MF
from observix.semconv import openinference as OI
from observix.testing import collect_spans, multi_collector


@observe(kind="chat", name="call_model")
def call_model(prompt: str) -> str:
    get_current_span().record_llm_call(
        provider="anthropic",
        request_model="claude-opus-4",
        input_messages=[{"role": "user", "content": prompt}],
        output_messages=[{"role": "assistant", "content": "Hi there"}],
        input_tokens=1200,
        output_tokens=340,
        temperature=0.7,
    ).set_session(user_id="u_1", session_id="s_9")
    return "Hi there"


class TestMultiBackendFanOut:
    """The central claim: one recording, four native renderings."""

    @pytest.fixture
    def collectors(self):
        with multi_collector(
            {
                "phoenix": "openinference",
                "langfuse": "langfuse",
                "mlflow": "mlflow",
                "datadog": "otel_genai",
            }
        ) as collectors:
            call_model("Hello")
            yield collectors

    def test_every_destination_receives_the_span(self, collectors) -> None:
        assert all(len(c) == 1 for c in collectors.values())

    def test_phoenix_receives_openinference(self, collectors) -> None:
        attrs = collectors["phoenix"].one().attributes
        assert attrs[OI.SPAN_KIND] == OI.KIND_LLM
        assert attrs[OI.LLM_TOKEN_COUNT_PROMPT] == 1200
        assert attrs["llm.input_messages.0.message.content"] == "Hello"

    def test_langfuse_receives_its_own_namespace(self, collectors) -> None:
        attrs = collectors["langfuse"].one().attributes
        assert attrs[LF.OBSERVATION_TYPE] == LF.TYPE_GENERATION
        assert "Hello" in attrs[LF.OBSERVATION_INPUT]
        assert json.loads(attrs[LF.OBSERVATION_USAGE_DETAILS])["input"] == 1200

    def test_mlflow_receives_mlflow_attributes(self, collectors) -> None:
        attrs = collectors["mlflow"].one().attributes
        assert attrs[MF.SPAN_TYPE] == MF.TYPE_CHAT_MODEL
        assert attrs[MF.LLM_MODEL] == "claude-opus-4"

    def test_datadog_receives_gen_ai(self, collectors) -> None:
        attrs = collectors["datadog"].one().attributes
        assert attrs[G.OPERATION_NAME] == G.OP_CHAT
        assert attrs[G.USAGE_INPUT_TOKENS] == 1200

    def test_no_destination_leaks_another_backends_vocabulary(self, collectors) -> None:
        """Emitting foreign namespaces makes backends render *worse*."""
        phoenix = collectors["phoenix"].one().attributes
        langfuse = collectors["langfuse"].one().attributes
        assert not any(k.startswith("langfuse.") for k in phoenix)
        assert not any(k.startswith("openinference.") for k in langfuse)

    def test_all_destinations_share_one_trace_id(self, collectors) -> None:
        trace_ids = {c.one().get_span_context().trace_id for c in collectors.values()}
        assert len(trace_ids) == 1


class TestPerDestinationRedaction:
    """Privacy is a property of the destination, not of the span."""

    @pytest.fixture
    def exporters(self):
        return {
            "full": InMemorySpanExporter(),
            "hashed": InMemorySpanExporter(),
            "none": InMemorySpanExporter(),
        }

    @pytest.fixture(autouse=True)
    def _configured(self, exporters):
        configure(
            service_name="redaction-test",
            set_global_tracer_provider=False,
            exporters=[
                ExporterConfig(
                    provider="memory",
                    name=name,
                    dialect="passthrough",
                    redact=None if name == "full" else name,
                    options={"exporter": exporter},
                )
                for name, exporter in exporters.items()
            ],
        )
        yield
        flush()
        shutdown()

    def test_one_span_three_privacy_levels(self, exporters) -> None:
        call_model("my email is alice@example.com")
        flush()

        full = exporters["full"].get_finished_spans()[0].attributes
        hashed = exporters["hashed"].get_finished_spans()[0].attributes
        none = exporters["none"].get_finished_spans()[0].attributes

        assert "alice@example.com" in full[C.INPUT_MESSAGES]
        assert hashed[C.INPUT_MESSAGES].startswith("sha256:")
        assert C.INPUT_MESSAGES not in none

    def test_metrics_survive_every_policy(self, exporters) -> None:
        call_model("anything")
        flush()
        for exporter in exporters.values():
            attrs = exporter.get_finished_spans()[0].attributes
            assert attrs[C.USAGE_INPUT_TOKENS] == 1200
            assert attrs[C.LLM_REQUEST_MODEL] == "claude-opus-4"


class TestPerDestinationSampling:
    def test_destinations_receive_different_volumes(self) -> None:
        everything = InMemorySpanExporter()
        nothing = InMemorySpanExporter()

        configure(
            service_name="sampling-test",
            set_global_tracer_provider=False,
            exporters=[
                ExporterConfig(
                    provider="memory",
                    name="all",
                    sample_ratio=1.0,
                    options={"exporter": everything},
                ),
                ExporterConfig(
                    provider="memory", name="none", sample_ratio=0.0, options={"exporter": nothing}
                ),
            ],
        )
        for i in range(20):
            call_model(f"prompt {i}")
        flush()

        assert len(everything.get_finished_spans()) == 20
        assert len(nothing.get_finished_spans()) == 0
        shutdown()


class TestNestedTraces:
    async def test_a_realistic_agent_trace(self) -> None:
        @observe(kind="agent", name="agent")
        async def agent(goal: str) -> str:
            with observe_block("retrieval", kind="retriever") as span:
                span.set_retrieval(query=goal, documents=["doc a", "doc b"], top_k=2)
            await asyncio.sleep(0)
            return call_model(goal)

        with collect_spans() as spans:
            await agent("find a hotel")

        assert set(spans.names()) == {"agent", "retrieval", "call_model"}

        agent_id = spans.first_named("agent").get_span_context().span_id
        assert spans.first_named("retrieval").parent.span_id == agent_id
        assert spans.first_named("call_model").parent.span_id == agent_id

    async def test_the_whole_tree_shares_one_trace_id(self) -> None:
        @observe(name="root")
        async def root() -> None:
            with observe_block("a"), observe_block("b"):
                pass

        with collect_spans() as spans:
            await root()

        trace_ids = {s.get_span_context().trace_id for s in spans}
        assert len(trace_ids) == 1


class TestLifecycle:
    def test_is_configured_reflects_state(self) -> None:
        assert is_configured() is False
        with collect_spans():
            assert is_configured() is True
        assert is_configured() is False

    def test_reconfiguring_replaces_the_previous_pipelines(self) -> None:
        configure(service_name="first", exporters=["memory"], set_global_tracer_provider=False)
        assert len(get_pipelines()) == 1

        configure(
            service_name="second",
            exporters=[{"provider": "memory", "name": "a"}, {"provider": "memory", "name": "b"}],
            set_global_tracer_provider=False,
        )
        assert len(get_pipelines()) == 2
        shutdown()

    def test_shutdown_is_idempotent(self) -> None:
        configure(service_name="x", exporters=["memory"], set_global_tracer_provider=False)
        shutdown()
        shutdown()  # must not raise

    def test_no_exporters_disables_rather_than_raising(self) -> None:
        configure(service_name="x", exporters=[], set_global_tracer_provider=False)
        assert is_configured() is False

    def test_enabled_false_records_nothing(self) -> None:
        memory = InMemorySpanExporter()
        configure(
            service_name="x",
            enabled=False,
            set_global_tracer_provider=False,
            exporters=[ExporterConfig(provider="memory", options={"exporter": memory})],
        )
        call_model("hello")
        assert memory.get_finished_spans() == []

    def test_an_unknown_provider_is_reported_with_alternatives(self) -> None:
        with pytest.raises((ProviderNotFoundError, ConfigurationError), match="phoenix"):
            configure(service_name="x", exporters=["phenix"], set_global_tracer_provider=False)

    def test_one_broken_destination_does_not_take_down_the_others(self) -> None:
        """A missing Langfuse key must not cost you Phoenix."""
        memory = InMemorySpanExporter()
        configure(
            service_name="x",
            set_global_tracer_provider=False,
            exporters=[
                ExporterConfig(provider="memory", name="good", options={"exporter": memory}),
                # No credentials available -> this destination fails to build.
                ExporterConfig(provider="langfuse", name="broken"),
            ],
        )
        assert [p.name for p in get_pipelines()] == ["good"]
        call_model("still works")
        flush()
        assert len(memory.get_finished_spans()) == 1
        shutdown()


class TestZeroOverheadWhenDisabled:
    def test_content_is_not_serialised_when_nothing_retains_it(self) -> None:
        memory = InMemorySpanExporter()
        configure(
            service_name="x",
            set_global_tracer_provider=False,
            exporters=[
                ExporterConfig(provider="memory", redact="none", options={"exporter": memory})
            ],
        )

        serialised = []

        class Tracked:
            def __repr__(self) -> str:
                serialised.append(1)
                return "<tracked>"

        @observe
        def work(obj: object) -> None: ...

        work(Tracked())
        flush()
        assert serialised == []  # never touched
        shutdown()
