"""Streaming responses: time-to-first-token and accumulated output.

Run:  python examples/06_streaming.py

A plain @observe would record an empty output here --- when the decorated
function returns, the stream has not produced anything yet. observe_stream
records chunks as they are consumed, so TTFT and the full response both land
on the span.
"""

import asyncio
import time

from observix import (
    configure,
    flush,
    get_current_span,
    observe,
    observe_astream,
    observe_stream,
    shutdown,
)
from observix.providers.memory import InMemorySpanExporter
from observix.semconv import canonical as C

memory = InMemorySpanExporter()
configure(
    service_name="streaming-demo",
    set_global_tracer_provider=False,
    exporters=[{"provider": "memory", "dialect": "passthrough", "options": {"exporter": memory}}],
)


def fake_openai_stream():
    """Mimics a vendor SDK: metadata frames first, then text deltas."""
    yield {"choices": [{"delta": {"role": "assistant"}}]}  # no text yet
    time.sleep(0.05)  # model thinking
    for word in ("Paris", " is", " the", " capital", " of", " France."):
        yield {"choices": [{"delta": {"content": word}}]}
        time.sleep(0.01)


@observe(kind="workflow", name="streamed_chat")
def streamed_chat(prompt: str):
    """Returning the stream: it gets its own child span.

    The decorated function returns immediately, so its span would close before
    a single chunk arrived. observe_stream therefore owns a span that lives
    exactly as long as the streaming does.
    """
    return observe_stream(
        fake_openai_stream(),
        name="openai_stream",
        provider="openai",
        request_model="gpt-4o",
        input_messages=[{"role": "user", "content": prompt}],
        input_tokens=12,
        output_tokens=8,
    )


async def fake_async_stream():
    for word in ("Streaming", " asynchronously", "."):
        await asyncio.sleep(0.01)
        yield word


@observe(kind="chat", name="async_streamed_chat")
async def async_streamed_chat():
    """Consuming the stream inside the function: record onto its own span.

    Here the decorated function is still running while chunks arrive, so
    passing span= keeps everything on one span instead of creating a child.
    """
    chunks = []
    async for chunk in observe_astream(
        fake_async_stream(),
        span=get_current_span(),
        provider="anthropic",
        request_model="claude-opus-4",
    ):
        chunks.append(chunk)
    return "".join(chunks)


def report(name: str) -> None:
    span = next(s for s in memory.get_finished_spans() if s.name == name)
    attrs = span.attributes
    print(f"\n--- {name} ---")
    print(f"  time to first token : {attrs.get(C.LLM_TIME_TO_FIRST_TOKEN_MS):.1f} ms")
    print(f"  streaming flag      : {attrs.get(C.LLM_STREAMING)}")
    print(f"  chunks              : {attrs.get(C.metadata_key('stream_chunks'))}")
    print(f"  output              : {str(attrs.get(C.OUTPUT_MESSAGES))[:90]}")
    cost = attrs.get(C.COST_TOTAL_USD)
    if cost is not None:
        print(f"  cost                : ${cost:.6f}")


if __name__ == "__main__":
    print(
        "consumed:",
        "".join(
            c["choices"][0]["delta"].get("content", "")
            for c in streamed_chat("What is the capital of France?")
        ),
    )
    print("consumed:", asyncio.run(async_streamed_chat()))

    # An abandoned stream still produces a complete span.
    @observe(kind="workflow", name="abandoned_parent")
    def abandoned():
        stream = observe_stream(fake_openai_stream(), name="abandoned_stream", provider="openai")
        next(stream)
        next(stream)
        stream.close()  # walk away mid-response

    abandoned()

    flush()
    for name in ("openai_stream", "async_streamed_chat", "abandoned_stream"):
        report(name)

    print("\nNote the TTFT on openai_stream: the ~50 ms of 'thinking' before the")
    print("first text chunk is exactly what a non-streaming span cannot show.")
    print("\nSpans recorded:", sorted(s.name for s in memory.get_finished_spans()))
    shutdown()
