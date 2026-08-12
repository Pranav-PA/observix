"""The @observe decorator and the context-manager / manual span APIs."""

from __future__ import annotations

import asyncio

import pytest

from observix import (
    get_current_span,
    observe,
    observe_block,
    start_span,
)
from observix.model.enums import SpanKind
from observix.semconv import canonical as C
from observix.testing import collect_spans

# --- Basic decoration --------------------------------------------------------


def test_bare_decorator_records_a_span() -> None:
    @observe
    def work(x: int) -> int:
        return x * 2

    with collect_spans() as spans:
        assert work(21) == 42

    span = spans.one()
    assert span.name == "test_bare_decorator_records_a_span.<locals>.work"
    assert span.attributes[C.KIND] == SpanKind.TASK.value


def test_parameterised_decorator_sets_name_and_kind() -> None:
    @observe(name="planner", kind="agent")
    def work() -> None:
        return None

    with collect_spans() as spans:
        work()

    span = spans.one()
    assert span.name == "planner"
    assert span.attributes[C.KIND] == SpanKind.AGENT.value


def test_decorator_preserves_function_metadata() -> None:
    @observe
    def documented(a: int, b: str = "x") -> None:
        """Docstring survives."""

    assert documented.__name__ == "documented"
    assert documented.__doc__ == "Docstring survives."


def test_unknown_kind_falls_back_to_task_rather_than_raising() -> None:
    @observe(kind="not-a-real-kind")
    def work() -> None: ...

    with collect_spans() as spans:
        work()

    assert spans.one().attributes[C.KIND] == SpanKind.TASK.value


# --- Input / output capture --------------------------------------------------


def test_captures_named_arguments() -> None:
    @observe
    def work(alpha: int, beta: str) -> str:
        return "result"

    with collect_spans() as spans:
        work(1, beta="two")

    attrs = spans.one().attributes
    assert '"alpha": 1' in attrs[C.INPUT]
    assert '"beta": "two"' in attrs[C.INPUT]
    assert attrs[C.OUTPUT] == "result"


def test_self_is_excluded_from_captured_input() -> None:
    class Service:
        @observe
        def method(self, value: int) -> int:
            return value

    with collect_spans() as spans:
        Service().method(7)

    captured = spans.one().attributes[C.INPUT]
    assert "self" not in captured
    assert '"value": 7' in captured


def test_exclude_args_omits_named_arguments() -> None:
    @observe(exclude_args=["secret"])
    def work(public: str, secret: str) -> None: ...

    with collect_spans() as spans:
        work("visible", secret="hidden")

    captured = spans.one().attributes[C.INPUT]
    assert "visible" in captured
    assert "hidden" not in captured


def test_capture_can_be_disabled() -> None:
    @observe(capture_input=False, capture_output=False)
    def work(value: str) -> str:
        return "output"

    with collect_spans() as spans:
        work("input")

    attrs = spans.one().attributes
    assert C.INPUT not in attrs
    assert C.OUTPUT not in attrs


def test_var_positional_arguments_are_captured() -> None:
    """*args functions take the slower full-binding path; verify it still works."""

    @observe
    def work(first: int, *rest: int, flag: bool = False) -> None: ...

    with collect_spans() as spans:
        work(1, 2, 3, flag=True)

    captured = spans.one().attributes[C.INPUT]
    assert '"first": 1' in captured
    assert '"flag": true' in captured


def test_keyword_only_arguments_are_captured() -> None:
    @observe
    def work(a: int, *, b: int) -> None: ...

    with collect_spans() as spans:
        work(1, b=2)

    captured = spans.one().attributes[C.INPUT]
    assert '"a": 1' in captured
    assert '"b": 2' in captured


def test_var_keyword_arguments_are_captured() -> None:
    @observe
    def work(a: int, **extra: int) -> None: ...

    with collect_spans() as spans:
        work(1, x=9)

    captured = spans.one().attributes[C.INPUT]
    assert '"a": 1' in captured
    assert '"x": 9' in captured


def test_defaults_not_passed_are_not_invented() -> None:
    """Only what the caller actually supplied should be recorded."""

    @observe
    def work(a: int, b: int = 5) -> None: ...

    with collect_spans() as spans:
        work(1)

    assert '"b"' not in spans.one().attributes[C.INPUT]


def test_builtins_are_decoratable() -> None:
    """Many builtins do expose a signature, so arguments are still named."""
    wrapped = observe(len)

    with collect_spans() as spans:
        assert wrapped([1, 2, 3]) == 3

    assert spans.one().attributes[C.INPUT] == '{"obj": [1, 2, 3]}'


def test_an_uninspectable_callable_falls_back_to_positional_keys() -> None:
    """Some C callables have no signature at all; capture must still work."""
    from observix.api import _bind_arguments, _SpanOptions

    options = _SpanOptions(
        name="x",
        kind=SpanKind.TASK,
        capture_input=True,
        capture_output=True,
        excluded_args=frozenset({"self"}),
        attributes=None,
        metadata=None,
        record_exceptions=True,
        signature=None,
    )
    assert _bind_arguments(options, ("a", "b"), {"kw": 1}) == {
        "arg0": "a",
        "arg1": "b",
        "kw": 1,
    }


def test_unserialisable_arguments_do_not_break_the_call() -> None:
    class Hostile:
        def __repr__(self) -> str:
            raise RuntimeError("repr exploded")

    @observe
    def work(obj: object) -> str:
        return "fine"

    with collect_spans() as spans:
        assert work(Hostile()) == "fine"

    assert len(spans) == 1


# --- Errors ------------------------------------------------------------------


def test_exception_is_recorded_and_re_raised() -> None:
    @observe
    def failing() -> None:
        raise ValueError("boom")

    with collect_spans() as spans, pytest.raises(ValueError, match="boom"):
        failing()

    span = spans.one()
    assert span.attributes[C.ERROR_TYPE] == "ValueError"
    assert span.status.status_code.name == "ERROR"


def test_span_still_ends_when_the_function_raises() -> None:
    @observe
    def failing() -> None:
        raise RuntimeError

    with collect_spans() as spans, pytest.raises(RuntimeError):
        failing()

    assert spans.one().end_time is not None


# --- Async -------------------------------------------------------------------


async def test_coroutine_is_traced() -> None:
    @observe(kind="agent")
    async def work(x: int) -> int:
        await asyncio.sleep(0)
        return x + 1

    with collect_spans() as spans:
        assert await work(1) == 2

    assert spans.one().attributes[C.KIND] == SpanKind.AGENT.value


async def test_async_context_propagates_across_await() -> None:
    @observe(name="child")
    async def child() -> None:
        await asyncio.sleep(0)

    @observe(name="parent")
    async def parent() -> None:
        await asyncio.sleep(0)
        await child()

    with collect_spans() as spans:
        await parent()

    child_span = spans.first_named("child")
    parent_span = spans.first_named("parent")
    assert child_span.parent is not None
    assert child_span.parent.span_id == parent_span.get_span_context().span_id


async def test_concurrent_tasks_get_sibling_spans() -> None:
    @observe(name="leaf")
    async def leaf(i: int) -> int:
        await asyncio.sleep(0)
        return i

    @observe(name="root")
    async def root() -> None:
        await asyncio.gather(*(leaf(i) for i in range(5)))

    with collect_spans() as spans:
        await root()

    root_id = spans.first_named("root").get_span_context().span_id
    leaves = spans.named("leaf")
    assert len(leaves) == 5
    assert all(leaf.parent.span_id == root_id for leaf in leaves)


async def test_coroutine_exception_is_recorded() -> None:
    @observe
    async def failing() -> None:
        raise ValueError("async boom")

    with collect_spans() as spans, pytest.raises(ValueError):
        await failing()

    assert spans.one().attributes[C.ERROR_TYPE] == "ValueError"


# --- Generators --------------------------------------------------------------


def test_generator_span_covers_the_whole_iteration() -> None:
    @observe
    def gen():
        yield 1
        yield 2
        yield 3

    with collect_spans() as spans:
        assert list(gen()) == [1, 2, 3]

    # Homogeneous scalars are stored as a native OTel array, not JSON.
    assert spans.one().attributes[C.OUTPUT] == (1, 2, 3)


def test_generator_is_not_traced_until_consumed() -> None:
    @observe
    def gen():
        yield 1

    with collect_spans() as spans:
        gen()  # created but never iterated
        assert len(spans) == 0


async def test_async_generator_is_traced() -> None:
    @observe
    async def gen():
        for i in range(3):
            await asyncio.sleep(0)
            yield i

    with collect_spans() as spans:
        assert [item async for item in gen()] == [0, 1, 2]

    assert spans.one().attributes[C.OUTPUT] == (0, 1, 2)


# --- observe_block -----------------------------------------------------------


def test_observe_block_records_a_span() -> None:
    with collect_spans() as spans, observe_block("retrieval", kind="retriever") as span:
        span.set_retrieval(query="hello", documents=["a", "b"], top_k=2)

    attrs = spans.one().attributes
    assert attrs[C.KIND] == SpanKind.RETRIEVER.value
    assert attrs[C.RETRIEVAL_QUERY] == "hello"
    assert attrs[C.RETRIEVAL_TOP_K] == 2


def test_observe_block_nests_under_a_decorated_function() -> None:
    @observe(name="outer")
    def outer() -> None:
        with observe_block("inner"):
            pass

    with collect_spans() as spans:
        outer()

    inner = spans.first_named("inner")
    outer_span = spans.first_named("outer")
    assert inner.parent.span_id == outer_span.get_span_context().span_id


def test_observe_block_records_exceptions() -> None:
    with collect_spans() as spans, pytest.raises(ValueError), observe_block("failing"):
        raise ValueError("inside block")

    assert spans.one().attributes[C.ERROR_TYPE] == "ValueError"


# --- Manual spans ------------------------------------------------------------


def test_start_span_requires_an_explicit_end() -> None:
    with collect_spans() as spans:
        span = start_span("manual", kind="workflow")
        span.set_metadata(stage="one")
        assert len(spans) == 0
        span.end()

    assert spans.one().attributes[C.metadata_key("stage")] == "one"


def test_get_current_span_returns_the_active_span() -> None:
    @observe(name="outer")
    def outer() -> None:
        get_current_span().set_metadata(touched=True)

    with collect_spans() as spans:
        outer()

    assert spans.one().attributes[C.metadata_key("touched")] is True


def test_get_current_span_is_safe_outside_any_span() -> None:
    with collect_spans():
        span = get_current_span()
        span.set_metadata(ignored=True)  # must not raise
        assert span.is_recording is False


# --- Disabled ----------------------------------------------------------------


def test_decorator_is_transparent_when_unconfigured() -> None:
    @observe
    def work(x: int) -> int:
        return x * 3

    assert work(5) == 15  # no configure() call at all


async def test_async_decorator_is_transparent_when_unconfigured() -> None:
    @observe
    async def work(x: int) -> int:
        return x * 3

    assert await work(5) == 15


def test_generator_is_transparent_when_unconfigured() -> None:
    @observe
    def gen():
        yield from (1, 2)

    assert list(gen()) == [1, 2]
