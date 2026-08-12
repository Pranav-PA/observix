# Quickstart

## Install

```bash
pip install 'observix[all]'
```

Or pick only what you need: `observix[otlp]`, `observix[langfuse]`, `observix[phoenix]`, …

## 1. Configure once, early

```python
from observix import configure

configure(service_name="my-app", exporters=["console"])
```

Call this once, before instrumented code runs — typically in `main()` or your app factory. Without it, `@observe` is a no-op costing a single attribute load.

## 2. Decorate

```python
from observix import observe


@observe
def handle(request):
    return process(request)
```

Arguments and return value are captured automatically. Sync functions, coroutines, generators and async generators all work.

```python
@observe(kind="agent", name="planner")
async def plan(goal: str) -> Plan: ...
```

`kind` is one of: `llm` · `chat` · `embedding` · `tool` · `agent` · `workflow` · `chain` · `retriever` · `reranker` · `guardrail` · `task`.

## 3. Record AI-specific detail

```python
from observix import get_current_span


@observe(kind="chat")
def ask(prompt: str) -> str:
    response = client.messages.create(...)

    get_current_span().record_llm_call(
        provider="anthropic",
        request_model="claude-opus-4",
        input_messages=[{"role": "user", "content": prompt}],
        output_messages=[{"role": "assistant", "content": response.text}],
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        temperature=0.7,
    )
    return response.text
```

Cost in USD is computed automatically when the model is in the [price book](configuration.md#cost).

`record_llm_call` accepts OpenAI-style dicts, Anthropic content blocks, plain strings, or already-canonical `Message` objects — it normalises all of them.

## 4. Instrument a block

```python
from observix import observe_block

with observe_block("retrieval", kind="retriever") as span:
    docs = vector_db.search(query, k=5)
    span.set_retrieval(query=query, documents=docs, top_k=5)
```

Spans nest automatically — anything called inside becomes a child, including across `await`.

## 5. Point at a real backend

Nothing above changes. Only configuration:

```python
configure(service_name="my-app", exporters=["phoenix", "langfuse"])
```

```bash
# Phoenix (local by default)
docker run -p 6006:6006 arizephoenix/phoenix

# Langfuse
export LANGFUSE_PUBLIC_KEY=pk-...
export LANGFUSE_SECRET_KEY=sk-...
```

Or with no code at all:

```bash
export OBSERVIX_EXPORTERS=phoenix,langfuse
```

## 6. Flush before a short-lived process exits

```python
from observix import flush

flush()  # scripts, serverless handlers, tests
```

Long-running services don't need this — an `atexit` hook handles normal shutdown.

## Common patterns

**Attach a user and session** so backends can group traces:

```python
get_current_span().set_session(user_id="u_1", session_id="s_9")
```

**Tool calls:**

```python
with observe_block("search", kind="tool") as span:
    result = search_api(query)
    span.set_tool(name="search", arguments={"q": query}, result=result)
```

**Continue a trace across a service boundary:**

```python
from observix import inject_context, extract_context, attach_context

headers = inject_context()  # caller
requests.post(url, headers=headers)

with attach_context(extract_context(request.headers)):  # callee
    handle()
```

## Testing your instrumentation

```python
from observix.testing import collect_spans


def test_records_token_usage():
    with collect_spans(dialect="openinference") as spans:
        ask("hello")
    assert spans.one().attributes["llm.token_count.prompt"] > 0
```

Spans arrive after redaction and translation, so you assert on exactly what the backend would receive.

## Next

- [Configuration reference](configuration.md)
- [Providers](providers.md) — the backends you can target
- [Dialects](dialects.md) — what each backend receives
- [Extending](extending.md) — add your own backend
- [Examples](../examples/)
