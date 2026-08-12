"""Dialect translation.

These are the highest-value tests in the suite: a dialect regression is
invisible in canonical attributes and only shows up as a degraded trace in
somebody's production backend.
"""

from __future__ import annotations

import json

import pytest

from observix.dialects import (
    CanonicalView,
    LangfuseDialect,
    MLflowDialect,
    OpenInferenceDialect,
    OTelGenAIDialect,
    PassthroughDialect,
    available_dialects,
    register_dialect,
    resolve_dialect,
    unregister_dialect,
)
from observix.dialects.base import Dialect, TranslationResult
from observix.errors import DialectNotFoundError
from observix.model.enums import SpanKind
from observix.semconv import canonical as C
from observix.semconv import genai as G
from observix.semconv import langfuse as LF
from observix.semconv import mlflow as MF
from observix.semconv import openinference as OI


@pytest.fixture
def llm_view() -> CanonicalView:
    """A representative chat-completion span."""
    return CanonicalView(
        {
            C.KIND: SpanKind.CHAT.value,
            C.LLM_PROVIDER: "anthropic",
            C.LLM_REQUEST_MODEL: "claude-opus-4",
            C.LLM_RESPONSE_MODEL: "claude-opus-4-20250101",
            C.LLM_REQUEST_TEMPERATURE: 0.7,
            C.LLM_REQUEST_MAX_TOKENS: 1024,
            C.LLM_RESPONSE_ID: "msg_123",
            C.LLM_RESPONSE_FINISH_REASONS: ("stop",),
            C.INPUT_MESSAGES: json.dumps(
                [{"role": "user", "parts": [{"type": "text", "content": "Hello"}]}]
            ),
            C.OUTPUT_MESSAGES: json.dumps(
                [{"role": "assistant", "parts": [{"type": "text", "content": "Hi!"}]}]
            ),
            C.SYSTEM_INSTRUCTIONS: "Be brief.",
            C.USAGE_INPUT_TOKENS: 1200,
            C.USAGE_OUTPUT_TOKENS: 340,
            C.USAGE_TOTAL_TOKENS: 1540,
            C.USAGE_CACHE_READ_INPUT_TOKENS: 200,
            C.COST_INPUT_USD: 0.018,
            C.COST_OUTPUT_USD: 0.0255,
            C.COST_TOTAL_USD: 0.0435,
            C.SESSION_ID: "s_9",
            C.USER_ID: "u_1",
            C.TAGS: ("prod", "beta"),
            C.metadata_key("experiment"): "arm_b",
            "http.method": "POST",
        },
        name="call_model",
    )


# --- CanonicalView -----------------------------------------------------------


class TestCanonicalView:
    def test_decodes_typed_fields(self, llm_view: CanonicalView) -> None:
        assert llm_view.kind is SpanKind.CHAT
        assert llm_view.provider == "anthropic"
        assert llm_view.model == "claude-opus-4-20250101"  # response wins
        assert llm_view.usage.input_tokens == 1200
        assert llm_view.cost.total_usd == 0.0435
        assert llm_view.tags == ["prod", "beta"]

    def test_decodes_messages(self, llm_view: CanonicalView) -> None:
        messages = llm_view.input_messages
        assert len(messages) == 1
        assert messages[0].role == "user"
        assert messages[0].text() == "Hello"

    def test_metadata_prefix_is_stripped(self, llm_view: CanonicalView) -> None:
        assert llm_view.metadata == {"experiment": "arm_b"}

    def test_foreign_attributes_are_identified(self, llm_view: CanonicalView) -> None:
        assert llm_view.passthrough_attributes() == {"http.method": "POST"}

    def test_missing_values_are_none_not_errors(self) -> None:
        view = CanonicalView({})
        assert view.kind is SpanKind.TASK
        assert view.model is None
        assert view.usage.is_empty()
        assert view.input_messages == []


# --- Shared behaviour --------------------------------------------------------

ALL_DIALECTS = [
    PassthroughDialect(),
    OTelGenAIDialect(),
    OpenInferenceDialect(),
    LangfuseDialect(),
    MLflowDialect(),
]


@pytest.mark.parametrize("dialect", ALL_DIALECTS, ids=lambda d: d.name)
def test_every_dialect_preserves_foreign_attributes(
    dialect: Dialect, llm_view: CanonicalView
) -> None:
    """User-set and third-party attributes must survive translation."""
    result = dialect(llm_view)
    assert result.attributes["http.method"] == "POST"


@pytest.mark.parametrize("dialect", ALL_DIALECTS, ids=lambda d: d.name)
def test_every_dialect_handles_an_empty_span(dialect: Dialect) -> None:
    result = dialect(CanonicalView({}, name="empty"))
    assert isinstance(result, TranslationResult)


@pytest.mark.parametrize("dialect", ALL_DIALECTS, ids=lambda d: d.name)
def test_no_dialect_emits_none_values(dialect: Dialect, llm_view: CanonicalView) -> None:
    """OTel rejects None attributes; TranslationResult.set must filter them."""
    result = dialect(llm_view)
    assert all(value is not None for value in result.attributes.values())


@pytest.mark.parametrize("dialect", ALL_DIALECTS, ids=lambda d: d.name)
def test_content_can_be_suppressed(llm_view: CanonicalView, dialect: Dialect) -> None:
    if dialect.name == "passthrough":
        pytest.skip("passthrough is the identity dialect by design")
    quiet = type(dialect)(capture_content=False)  # type: ignore[call-arg]
    rendered = json.dumps(quiet(llm_view).attributes)
    assert "Hello" not in rendered
    assert "Hi!" not in rendered


# --- OpenInference -----------------------------------------------------------


class TestOpenInferenceDialect:
    def test_maps_span_kind_and_model(self, llm_view: CanonicalView) -> None:
        attrs = OpenInferenceDialect()(llm_view).attributes
        assert attrs[OI.SPAN_KIND] == OI.KIND_LLM
        assert attrs[OI.LLM_MODEL_NAME] == "claude-opus-4-20250101"
        assert attrs[OI.LLM_PROVIDER] == "anthropic"

    def test_maps_token_counts(self, llm_view: CanonicalView) -> None:
        attrs = OpenInferenceDialect()(llm_view).attributes
        assert attrs[OI.LLM_TOKEN_COUNT_PROMPT] == 1200
        assert attrs[OI.LLM_TOKEN_COUNT_COMPLETION] == 340
        assert attrs[OI.LLM_TOKEN_COUNT_TOTAL] == 1540
        assert attrs[OI.LLM_TOKEN_COUNT_PROMPT_CACHE_HIT] == 200

    def test_flattens_messages_with_indices(self, llm_view: CanonicalView) -> None:
        """Phoenix reads indexed keys; a JSON blob would not render per-message."""
        attrs = OpenInferenceDialect()(llm_view).attributes
        assert attrs["llm.input_messages.0.message.role"] == "user"
        assert attrs["llm.input_messages.0.message.content"] == "Hello"
        assert attrs["llm.output_messages.0.message.role"] == "assistant"
        assert attrs["llm.output_messages.0.message.content"] == "Hi!"

    def test_collapses_request_parameters_into_one_json_blob(self, llm_view: CanonicalView) -> None:
        attrs = OpenInferenceDialect()(llm_view).attributes
        params = json.loads(attrs[OI.LLM_INVOCATION_PARAMETERS])
        assert params == {"temperature": 0.7, "max_tokens": 1024}

    def test_json_content_is_labelled_as_json_not_text(self, llm_view: CanonicalView) -> None:
        """Regression: canonical attrs hold JSON *strings*; a naive isinstance
        check mislabelled every message list as text/plain."""
        attrs = OpenInferenceDialect()(llm_view).attributes
        assert attrs[OI.INPUT_MIME_TYPE] == OI.MIME_JSON
        assert attrs[OI.OUTPUT_MIME_TYPE] == OI.MIME_JSON

    def test_plain_text_content_is_labelled_as_text(self) -> None:
        view = CanonicalView({C.KIND: "task", C.INPUT: "just a string"})
        attrs = OpenInferenceDialect()(view).attributes
        assert attrs[OI.INPUT_MIME_TYPE] == OI.MIME_TEXT

    def test_embedding_uses_the_embedding_model_key(self) -> None:
        view = CanonicalView(
            {C.KIND: SpanKind.EMBEDDING.value, C.LLM_REQUEST_MODEL: "text-embedding-3-small"}
        )
        attrs = OpenInferenceDialect()(view).attributes
        assert attrs[OI.SPAN_KIND] == OI.KIND_EMBEDDING
        assert attrs[OI.EMBEDDING_MODEL_NAME] == "text-embedding-3-small"
        assert OI.LLM_MODEL_NAME not in attrs

    def test_flattens_retrieved_documents(self) -> None:
        view = CanonicalView(
            {
                C.KIND: SpanKind.RETRIEVER.value,
                C.RETRIEVAL_DOCUMENTS: json.dumps([{"id": "d1", "content": "alpha", "score": 0.9}]),
            }
        )
        attrs = OpenInferenceDialect()(view).attributes
        assert attrs["retrieval.documents.0.document.id"] == "d1"
        assert attrs["retrieval.documents.0.document.content"] == "alpha"
        assert attrs["retrieval.documents.0.document.score"] == 0.9

    def test_flattens_tool_calls_in_messages(self) -> None:
        view = CanonicalView(
            {
                C.KIND: SpanKind.CHAT.value,
                C.OUTPUT_MESSAGES: json.dumps(
                    [
                        {
                            "role": "assistant",
                            "parts": [
                                {
                                    "type": "tool_call",
                                    "id": "call_1",
                                    "name": "get_weather",
                                    "content": {"city": "Paris"},
                                }
                            ],
                        }
                    ]
                ),
            }
        )
        attrs = OpenInferenceDialect()(view).attributes
        prefix = "llm.output_messages.0.message.tool_calls.0"
        assert attrs[f"{prefix}.tool_call.id"] == "call_1"
        assert attrs[f"{prefix}.tool_call.function.name"] == "get_weather"


# --- Langfuse ----------------------------------------------------------------


class TestLangfuseDialect:
    def test_chat_becomes_a_generation(self, llm_view: CanonicalView) -> None:
        attrs = LangfuseDialect()(llm_view).attributes
        assert attrs[LF.OBSERVATION_TYPE] == LF.TYPE_GENERATION
        assert attrs[LF.OBSERVATION_MODEL_NAME] == "claude-opus-4-20250101"

    def test_writes_explicit_input_output(self, llm_view: CanonicalView) -> None:
        """The whole point: langfuse.* is highest precedence, so write it
        rather than hoping Langfuse infers content from gen_ai.*."""
        attrs = LangfuseDialect()(llm_view).attributes
        assert "Hello" in attrs[LF.OBSERVATION_INPUT]
        assert "Hi!" in attrs[LF.OBSERVATION_OUTPUT]

    def test_usage_is_a_details_object(self, llm_view: CanonicalView) -> None:
        details = json.loads(LangfuseDialect()(llm_view).attributes[LF.OBSERVATION_USAGE_DETAILS])
        assert details["input"] == 1200
        assert details["output"] == 340
        assert details["total"] == 1540
        assert details["cache_read_input_tokens"] == 200

    def test_cost_is_a_details_object(self, llm_view: CanonicalView) -> None:
        details = json.loads(LangfuseDialect()(llm_view).attributes[LF.OBSERVATION_COST_DETAILS])
        assert details == {"input": 0.018, "output": 0.0255, "total": 0.0435}

    def test_identity_is_mapped(self, llm_view: CanonicalView) -> None:
        attrs = LangfuseDialect()(llm_view).attributes
        assert attrs[LF.USER_ID] == "u_1"
        assert attrs[LF.SESSION_ID] == "s_9"
        assert attrs[LF.TRACE_TAGS] == ["prod", "beta"]

    def test_metadata_is_namespaced(self, llm_view: CanonicalView) -> None:
        attrs = LangfuseDialect()(llm_view).attributes
        assert attrs["langfuse.observation.metadata.experiment"] == "arm_b"

    def test_errors_raise_the_observation_level(self) -> None:
        view = CanonicalView({C.KIND: "task", C.ERROR_TYPE: "ValueError"})
        attrs = LangfuseDialect()(view).attributes
        assert attrs[LF.OBSERVATION_LEVEL] == LF.LEVEL_ERROR
        assert attrs[LF.OBSERVATION_STATUS_MESSAGE] == "ValueError"

    def test_tool_payloads_become_input_output(self) -> None:
        view = CanonicalView(
            {
                C.KIND: SpanKind.TOOL.value,
                C.TOOL_NAME: "search",
                C.TOOL_ARGUMENTS: '{"q": "hotels"}',
                C.TOOL_RESULT: "3 results",
            }
        )
        attrs = LangfuseDialect()(view).attributes
        assert attrs[LF.OBSERVATION_TYPE] == LF.TYPE_TOOL
        assert attrs[LF.OBSERVATION_INPUT] == '{"q": "hotels"}'
        assert attrs[LF.OBSERVATION_OUTPUT] == "3 results"


# --- OTel GenAI --------------------------------------------------------------


class TestOTelGenAIDialect:
    def test_maps_operation_and_provider(self, llm_view: CanonicalView) -> None:
        attrs = OTelGenAIDialect()(llm_view).attributes
        assert attrs[G.OPERATION_NAME] == G.OP_CHAT
        assert attrs[G.PROVIDER_NAME] == "anthropic"
        assert attrs[G.REQUEST_MODEL] == "claude-opus-4"

    def test_maps_token_usage(self, llm_view: CanonicalView) -> None:
        attrs = OTelGenAIDialect()(llm_view).attributes
        assert attrs[G.USAGE_INPUT_TOKENS] == 1200
        assert attrs[G.USAGE_OUTPUT_TOKENS] == 340
        assert attrs[G.USAGE_CACHE_READ_INPUT_TOKENS] == 200

    def test_emits_both_structured_and_legacy_content(self, llm_view: CanonicalView) -> None:
        """Backends that never adopted the v1.37 structured keys still need
        gen_ai.prompt / gen_ai.completion to render anything."""
        attrs = OTelGenAIDialect()(llm_view).attributes
        assert G.INPUT_MESSAGES in attrs
        assert "Hello" in attrs[G.LEGACY_PROMPT]
        assert "Hi!" in attrs[G.LEGACY_COMPLETION]

    def test_legacy_content_can_be_disabled(self, llm_view: CanonicalView) -> None:
        attrs = OTelGenAIDialect(legacy_content=False)(llm_view).attributes
        assert G.LEGACY_PROMPT not in attrs

    def test_renames_span_to_operation_and_model(self, llm_view: CanonicalView) -> None:
        assert OTelGenAIDialect()(llm_view).name == "chat claude-opus-4"

    def test_tool_span_is_named_after_the_tool(self) -> None:
        view = CanonicalView({C.KIND: SpanKind.TOOL.value, C.TOOL_NAME: "search"})
        assert OTelGenAIDialect()(view).name == "execute_tool search"

    def test_cost_survives_as_canonical_since_gen_ai_has_no_home_for_it(
        self, llm_view: CanonicalView
    ) -> None:
        attrs = OTelGenAIDialect()(llm_view).attributes
        assert attrs[C.COST_TOTAL_USD] == 0.0435


# --- MLflow ------------------------------------------------------------------


class TestMLflowDialect:
    def test_maps_span_type_and_model(self, llm_view: CanonicalView) -> None:
        attrs = MLflowDialect()(llm_view).attributes
        assert attrs[MF.SPAN_TYPE] == MF.TYPE_CHAT_MODEL
        assert attrs[MF.LLM_MODEL] == "claude-opus-4-20250101"
        assert attrs[MF.LLM_PROVIDER] == "anthropic"

    def test_usage_is_json(self, llm_view: CanonicalView) -> None:
        usage = json.loads(MLflowDialect()(llm_view).attributes[MF.CHAT_USAGE])
        assert usage == {"input_tokens": 1200, "output_tokens": 340, "total_tokens": 1540}

    def test_plain_string_io_is_wrapped_as_valid_json(self) -> None:
        """MLflow requires spanInputs to parse as JSON."""
        view = CanonicalView({C.KIND: "task", C.INPUT: "plain", C.OUTPUT: "text"})
        attrs = MLflowDialect()(view).attributes
        assert json.loads(attrs[MF.SPAN_INPUTS]) == "plain"
        assert json.loads(attrs[MF.SPAN_OUTPUTS]) == "text"

    def test_existing_json_is_not_double_encoded(self, llm_view: CanonicalView) -> None:
        attrs = MLflowDialect()(llm_view).attributes
        assert isinstance(json.loads(attrs[MF.SPAN_INPUTS]), list)


# --- Registry ----------------------------------------------------------------


class TestDialectRegistry:
    def test_builtins_are_available(self) -> None:
        names = available_dialects()
        for expected in ("passthrough", "otel_genai", "openinference", "langfuse", "mlflow"):
            assert expected in names

    def test_resolves_by_name(self) -> None:
        assert isinstance(resolve_dialect("openinference"), OpenInferenceDialect)

    def test_resolves_instances_and_classes(self) -> None:
        instance = OpenInferenceDialect()
        assert resolve_dialect(instance) is instance
        assert isinstance(resolve_dialect(OpenInferenceDialect), OpenInferenceDialect)

    def test_unknown_name_lists_the_alternatives(self) -> None:
        with pytest.raises(DialectNotFoundError, match="openinference"):
            resolve_dialect("nope")

    def test_third_party_registration(self) -> None:
        class CustomDialect(Dialect):
            name = "custom"

            def translate(self, view: CanonicalView) -> TranslationResult:
                return TranslationResult({"custom.kind": view.kind.value})

        register_dialect("custom", CustomDialect)
        try:
            attrs = resolve_dialect("custom")(CanonicalView({C.KIND: "agent"})).attributes
            assert attrs["custom.kind"] == "agent"
        finally:
            unregister_dialect("custom")

    def test_names_are_case_and_separator_insensitive(self) -> None:
        assert isinstance(resolve_dialect("OTEL-GENAI"), OTelGenAIDialect)
