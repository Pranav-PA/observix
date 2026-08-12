"""Canonical model: messages, usage, cost, serialisation."""

from __future__ import annotations

import pytest

from observix._serde import as_text, from_json, to_json
from observix.cost import ModelPrice, compute_cost, get_price, register_price
from observix.model.enums import PartType, Role, SpanKind
from observix.model.messages import (
    flatten_text,
    messages_from_dicts,
    messages_to_dicts,
    normalize_messages,
)
from observix.model.usage import Cost, TokenUsage


class TestSpanKind:
    def test_coerces_known_strings(self) -> None:
        assert SpanKind.coerce("chat") is SpanKind.CHAT
        assert SpanKind.coerce("  AGENT  ") is SpanKind.AGENT

    def test_unknown_values_fall_back_rather_than_raising(self) -> None:
        """A typo in kind= must never break the application."""
        assert SpanKind.coerce("nonsense") is SpanKind.TASK
        assert SpanKind.coerce(None) is SpanKind.TASK
        assert SpanKind.coerce(42) is SpanKind.TASK

    def test_model_call_classification(self) -> None:
        assert SpanKind.CHAT.is_model_call()
        assert SpanKind.EMBEDDING.is_model_call()
        assert not SpanKind.TOOL.is_model_call()


class TestRoleNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("user", "user"),
            ("USER", "user"),
            ("human", "user"),
            ("ai", "assistant"),
            ("model", "assistant"),
            ("bot", "assistant"),
            ("function", "tool"),
            ("system", "system"),
        ],
    )
    def test_vendor_aliases_are_normalised(self, raw: str, expected: str) -> None:
        assert Role.coerce(raw) == expected

    def test_unknown_roles_are_preserved(self) -> None:
        assert Role.coerce("developer") == "developer"


class TestMessageNormalisation:
    def test_openai_style_dicts(self) -> None:
        messages = normalize_messages(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        )
        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[0].text() == "hi"

    def test_a_bare_string_becomes_a_user_message(self) -> None:
        messages = normalize_messages("just text")
        assert messages[0].role == "user"
        assert messages[0].text() == "just text"

    def test_anthropic_style_content_blocks(self) -> None:
        messages = normalize_messages(
            [{"role": "user", "content": [{"type": "text", "text": "block one"}]}]
        )
        assert messages[0].text() == "block one"

    def test_anthropic_tool_use(self) -> None:
        messages = normalize_messages(
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "x"}}
                    ],
                }
            ]
        )
        part = messages[0].parts[0]
        assert part.type == PartType.TOOL_CALL.value
        assert part.id == "t1"
        assert part.name == "search"

    def test_openai_tool_calls_sit_beside_content(self) -> None:
        messages = normalize_messages(
            [
                {
                    "role": "assistant",
                    "content": "thinking",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                        }
                    ],
                }
            ]
        )
        types = [p.type for p in messages[0].parts]
        assert PartType.TEXT.value in types
        assert PartType.TOOL_CALL.value in types

    def test_images_are_recorded_by_reference_not_value(self) -> None:
        """Embedding base64 payloads in a span is never right."""
        messages = normalize_messages(
            [{"role": "user", "content": [{"type": "image", "source": {"data": "AAAA" * 10_000}}]}]
        )
        part = messages[0].parts[0]
        assert part.type == PartType.BLOB.value
        assert "AAAA" not in str(part.content)

    def test_reasoning_blocks(self) -> None:
        messages = normalize_messages(
            [{"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"}]}]
        )
        assert messages[0].parts[0].type == PartType.REASONING.value

    def test_none_yields_nothing(self) -> None:
        assert normalize_messages(None) == []

    def test_round_trips_through_dicts(self) -> None:
        original = normalize_messages([{"role": "user", "content": "hello"}])
        restored = messages_from_dicts(messages_to_dicts(original))
        assert restored[0].role == "user"
        assert restored[0].text() == "hello"

    def test_flatten_text(self) -> None:
        messages = normalize_messages(
            [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
        )
        assert flatten_text(messages) == "user: q\n\nassistant: a"

    def test_arbitrary_objects_do_not_raise(self) -> None:
        class Weird:
            pass

        assert len(normalize_messages([Weird()])) == 1


class TestTokenUsage:
    def test_total_is_derived_when_absent(self) -> None:
        assert TokenUsage(input_tokens=100, output_tokens=50).resolved_total() == 150

    def test_an_explicit_total_from_the_provider_wins(self) -> None:
        usage = TokenUsage(input_tokens=100, output_tokens=50, total_tokens=175)
        assert usage.resolved_total() == 175

    def test_no_data_means_no_total(self) -> None:
        assert TokenUsage().resolved_total() is None
        assert TokenUsage().is_empty()

    def test_a_single_side_still_derives(self) -> None:
        assert TokenUsage(input_tokens=10).resolved_total() == 10


class TestCostComputation:
    def test_prices_a_known_model(self) -> None:
        cost = compute_cost(
            model="claude-opus-4", usage=TokenUsage(input_tokens=1_000_000, output_tokens=0)
        )
        assert cost is not None
        assert cost.input_usd == pytest.approx(15.0)

    def test_unknown_models_are_not_guessed(self) -> None:
        assert compute_cost(model="totally-unknown-model", usage=TokenUsage(input_tokens=1)) is None

    def test_version_suffixes_resolve_to_the_base_model(self) -> None:
        assert get_price("claude-opus-4-20250101") is not None

    def test_vendor_route_prefixes_are_stripped(self) -> None:
        assert get_price("anthropic/claude-sonnet-4") is not None
        assert get_price("us.anthropic.claude-sonnet-4") is not None

    def test_longest_prefix_wins(self) -> None:
        """gpt-4o-mini must not be priced as the far pricier gpt-4o."""
        mini = get_price("gpt-4o-mini")
        full = get_price("gpt-4o")
        assert mini is not None and full is not None
        assert mini.input < full.input

    def test_cached_reads_are_not_billed_twice(self) -> None:
        """Providers report cache reads inside input_tokens."""
        register_price("cache-test", ModelPrice(input=10.0, output=10.0, cache_read=1.0))
        cost = compute_cost(
            model="cache-test",
            usage=TokenUsage(
                input_tokens=1_000_000, cache_read_input_tokens=900_000, output_tokens=0
            ),
        )
        assert cost is not None
        # 100k at $10/M + 900k at $1/M = $1.00 + $0.90
        assert cost.input_usd == pytest.approx(1.9)

    def test_custom_prices_override_builtins(self) -> None:
        register_price("gpt-4o", ModelPrice(input=999.0, output=999.0))
        price = get_price("gpt-4o")
        assert price is not None and price.input == 999.0

    def test_total_is_the_sum(self) -> None:
        cost = compute_cost(
            model="claude-opus-4",
            usage=TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        )
        assert cost is not None
        assert cost.total_usd == pytest.approx(cost.input_usd + cost.output_usd)


class TestCost:
    def test_total_is_derived(self) -> None:
        assert Cost(input_usd=1.0, output_usd=2.0).resolved_total() == 3.0

    def test_empty_cost(self) -> None:
        assert Cost().is_empty()
        assert Cost().resolved_total() is None


class TestSerialisation:
    def test_round_trips_plain_data(self) -> None:
        assert from_json(to_json({"a": [1, 2]})) == {"a": [1, 2]}

    def test_dataclasses(self) -> None:
        from dataclasses import dataclass

        @dataclass
        class Point:
            x: int
            y: int

        assert from_json(to_json(Point(1, 2))) == {"x": 1, "y": 2}

    def test_a_hostile_repr_does_not_raise(self) -> None:
        class Hostile:
            def __repr__(self) -> str:
                raise RuntimeError("nope")

        assert isinstance(to_json(Hostile()), str)

    def test_circular_references_do_not_raise(self) -> None:
        data: dict = {}
        data["self"] = data
        assert isinstance(to_json(data), str)

    def test_bytes_are_summarised_not_embedded(self) -> None:
        assert "bytes" in to_json(b"x" * 1000)

    def test_invalid_json_parses_to_none(self) -> None:
        assert from_json("{not json") is None

    def test_as_text_passes_strings_through(self) -> None:
        assert as_text("already text") == "already text"
        assert as_text({"a": 1}) == '{"a": 1}'
