"""Conformance against the upstream semantic-convention packages.

observix hardcodes the attribute names each backend reads. If OpenInference or
the OpenTelemetry GenAI conventions rename one, our constant silently drifts and
every user of that dialect gets a degraded trace --- rendering fine, simply
missing data. Nothing in the rest of the suite can catch that, because our
dialect tests assert against the very constants that would be wrong.

These tests diff our constants against the official packages, so an upstream
rename fails CI here rather than in somebody's production trace view.

Both packages are pure constants and are pinned as dev dependencies, so these
run on every commit.
"""

from __future__ import annotations

import pytest

from observix.model.enums import SpanKind
from observix.semconv import genai as G
from observix.semconv import openinference as OI

openinference_semconv = pytest.importorskip(
    "openinference.semconv.trace",
    reason="pip install openinference-semantic-conventions",
)


def _official_openinference() -> dict[str, str]:
    """Every attribute name the official OpenInference package defines."""
    from openinference.semconv.trace import (
        DocumentAttributes,
        EmbeddingAttributes,
        MessageAttributes,
        SpanAttributes,
        ToolCallAttributes,
    )

    names: dict[str, str] = {}
    for cls in (
        SpanAttributes,
        MessageAttributes,
        DocumentAttributes,
        ToolCallAttributes,
        EmbeddingAttributes,
    ):
        for attr in dir(cls):
            if not attr.startswith("_"):
                value = getattr(cls, attr)
                if isinstance(value, str):
                    names[value] = f"{cls.__name__}.{attr}"
    return names


def _our_openinference_attributes() -> dict[str, str]:
    """Our OpenInference attribute-name constants, excluding kinds and MIME types."""
    skip_prefixes = ("KIND_", "MIME_")
    return {
        name: getattr(OI, name)
        for name in dir(OI)
        if name.isupper()
        and isinstance(getattr(OI, name), str)
        and not name.startswith(skip_prefixes)
    }


class TestOpenInferenceConformance:
    """Phoenix and Arize read these names. A typo costs the user their trace view."""

    def test_every_attribute_name_exists_upstream(self) -> None:
        official = _official_openinference()
        drifted = {
            name: value
            for name, value in _our_openinference_attributes().items()
            if value not in official
        }
        assert not drifted, (
            "These observix constants are not defined by the official "
            f"openinference-semantic-conventions package: {drifted}"
        )

    def test_every_span_kind_is_valid_upstream(self) -> None:
        from openinference.semconv.trace import OpenInferenceSpanKindValues

        official = {v.value for v in OpenInferenceSpanKindValues}
        ours = {getattr(OI, n) for n in dir(OI) if n.startswith("KIND_")}
        assert ours <= official, f"Unrecognised span kinds: {sorted(ours - official)}"

    def test_flattened_message_keys_match_upstream_construction(self) -> None:
        """Phoenix reads indexed keys; an off-by-one in the shape renders nothing."""
        from openinference.semconv.trace import MessageAttributes, SpanAttributes

        assert OI.message_key(OI.LLM_INPUT_MESSAGES, 0, OI.MESSAGE_ROLE) == (
            f"{SpanAttributes.LLM_INPUT_MESSAGES}.0.{MessageAttributes.MESSAGE_ROLE}"
        )
        assert OI.message_key(OI.LLM_OUTPUT_MESSAGES, 3, OI.MESSAGE_CONTENT) == (
            f"{SpanAttributes.LLM_OUTPUT_MESSAGES}.3.{MessageAttributes.MESSAGE_CONTENT}"
        )

    def test_flattened_document_keys_match_upstream_construction(self) -> None:
        from openinference.semconv.trace import DocumentAttributes, SpanAttributes

        assert OI.document_key(0, OI.DOCUMENT_CONTENT) == (
            f"{SpanAttributes.RETRIEVAL_DOCUMENTS}.0.{DocumentAttributes.DOCUMENT_CONTENT}"
        )

    def test_tool_call_arguments_use_the_json_variant(self) -> None:
        """Upstream names this constant ...ARGUMENTS_JSON but its value has no
        `_json` suffix. Easy to mirror wrongly from the constant name."""
        from openinference.semconv.trace import ToolCallAttributes

        assert OI.TOOL_CALL_FUNCTION_ARGUMENTS == (
            ToolCallAttributes.TOOL_CALL_FUNCTION_ARGUMENTS_JSON
        )

    def test_cache_token_details_match(self) -> None:
        """Nested `prompt_details` paths are the easiest to get subtly wrong."""
        from openinference.semconv.trace import SpanAttributes

        assert OI.LLM_TOKEN_COUNT_PROMPT_CACHE_HIT == (
            SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_READ
        )
        assert OI.LLM_TOKEN_COUNT_PROMPT_CACHE_WRITE == (
            SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_WRITE
        )
        assert OI.LLM_TOKEN_COUNT_COMPLETION_REASONING == (
            SpanAttributes.LLM_TOKEN_COUNT_COMPLETION_DETAILS_REASONING
        )


class TestOTelGenAIConformance:
    """Datadog, Grafana, Honeycomb and SigNoz read these names."""

    @staticmethod
    def _official() -> set[str]:
        gen_ai = pytest.importorskip(
            "opentelemetry.semconv._incubating.attributes.gen_ai_attributes",
            reason="incubating gen_ai attributes not available in this SDK build",
        )
        return {
            getattr(gen_ai, n)
            for n in dir(gen_ai)
            if n.isupper() and isinstance(getattr(gen_ai, n), str)
        }

    #: Deliberate extensions: names real instrumentation libraries emit that the
    #: specification does not define. Each needs a reason, so this list cannot
    #: quietly become a dumping ground for typos.
    DELIBERATE_EXTENSIONS = {
        # Emitted by opentelemetry-instrumentation-openai (OpenLLMetry) and
        # verified in tests/test_foreign_instrumentation.py. Worth adopting
        # because providers occasionally report a total that is not the sum.
        "gen_ai.usage.total_tokens",
    }

    def test_attribute_names_exist_upstream(self) -> None:
        official = self._official()
        ours = {
            name: getattr(G, name)
            for name in dir(G)
            if name.isupper()
            and isinstance(getattr(G, name), str)
            and getattr(G, name).startswith("gen_ai.")
        }
        drifted = {
            n: v
            for n, v in ours.items()
            if v not in official and v not in self.DELIBERATE_EXTENSIONS
        }
        assert not drifted, (
            "These observix gen_ai constants are not defined by the installed "
            "opentelemetry-semantic-conventions. Either they drifted, or they are "
            "deliberate extensions and belong in DELIBERATE_EXTENSIONS with a "
            f"reason: {drifted}"
        )

    def test_deliberate_extensions_are_still_necessary(self) -> None:
        """If upstream adopts one of our extensions, stop calling it an extension."""
        official = self._official()
        adopted_upstream = self.DELIBERATE_EXTENSIONS & official
        assert not adopted_upstream, (
            "Upstream now defines these, so remove them from "
            f"DELIBERATE_EXTENSIONS: {sorted(adopted_upstream)}"
        )

    def test_operation_names_exist_upstream(self) -> None:
        """`gen_ai.operation.name` values are a closed vocabulary."""
        gen_ai = pytest.importorskip(
            "opentelemetry.semconv._incubating.attributes.gen_ai_attributes",
            reason="incubating gen_ai attributes not available",
        )
        enum = getattr(gen_ai, "GenAiOperationNameValues", None)
        if enum is None:
            pytest.skip("GenAiOperationNameValues not present in this SDK build")

        official = {member.value for member in enum}
        ours = {
            G.OP_CHAT,
            G.OP_EMBEDDINGS,
            G.OP_EXECUTE_TOOL,
            G.OP_INVOKE_AGENT,
            G.OP_CREATE_AGENT,
        }
        assert ours <= official, f"Unrecognised operations: {sorted(ours - official)}"


class TestKindCoverage:
    """Every canonical kind must translate; an unmapped kind silently degrades."""

    def test_every_canonical_kind_maps_to_openinference(self) -> None:
        from observix.dialects.openinference import _KIND_MAP

        missing = [k for k in SpanKind if k not in _KIND_MAP]
        assert not missing, f"Kinds with no OpenInference mapping: {missing}"

    def test_every_canonical_kind_maps_to_langfuse(self) -> None:
        from observix.dialects.langfuse import _TYPE_MAP

        missing = [k for k in SpanKind if k not in _TYPE_MAP]
        assert not missing, f"Kinds with no Langfuse mapping: {missing}"

    def test_every_canonical_kind_maps_to_mlflow(self) -> None:
        from observix.dialects.mlflow import _TYPE_MAP

        missing = [k for k in SpanKind if k not in _TYPE_MAP]
        assert not missing, f"Kinds with no MLflow mapping: {missing}"
