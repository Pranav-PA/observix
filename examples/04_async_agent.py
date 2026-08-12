"""Async agent: nested spans across await, concurrency, and tool calls.

Run:  python examples/04_async_agent.py

Context propagation rides on contextvars, so parent/child relationships survive
`await` and `asyncio.gather` with no extra work from you.
"""

import asyncio
import random

from observix import configure, flush, get_current_span, observe, observe_block


@observe(kind="retriever", name="search")
async def search(query: str, k: int = 3) -> list[dict]:
    await asyncio.sleep(0.01)
    docs = [
        {"id": f"doc_{i}", "content": f"Result {i} for {query}", "score": 1.0 - i * 0.1}
        for i in range(k)
    ]
    get_current_span().set_retrieval(query=query, documents=docs, top_k=k)
    return docs


@observe(kind="tool", name="lookup_price")
async def lookup_price(item: str) -> float:
    await asyncio.sleep(0.01)
    price = round(random.uniform(50, 500), 2)
    get_current_span().set_tool(name="lookup_price", arguments={"item": item}, result=price)
    return price


@observe(kind="chat", name="generate")
async def generate(query: str, context: list[dict]) -> str:
    await asyncio.sleep(0.02)
    answer = f"Based on {len(context)} sources: here is your answer."
    get_current_span().record_llm_call(
        provider="anthropic",
        request_model="claude-opus-4",
        input_messages=[
            {"role": "system", "content": "Answer using the provided context."},
            {"role": "user", "content": query},
        ],
        output_messages=[{"role": "assistant", "content": answer}],
        input_tokens=1450,
        output_tokens=64,
        temperature=0.2,
    )
    return answer


@observe(kind="agent", name="agent")
async def agent(query: str) -> str:
    """Concurrent children still nest correctly under this span."""
    docs, *prices = await asyncio.gather(
        search(query),
        lookup_price("widget-a"),
        lookup_price("widget-b"),
    )

    with observe_block("rerank", kind="reranker") as span:
        ranked = sorted(docs, key=lambda d: -d["score"])
        span.set_metadata(candidates=len(docs), prices=prices)

    return await generate(query, ranked)


async def main() -> None:
    configure(service_name="async-agent", exporters=["console"])
    print(await agent("what is the price of a widget?"))
    flush()


if __name__ == "__main__":
    asyncio.run(main())
