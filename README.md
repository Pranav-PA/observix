# observix

**Provider-agnostic observability for Python and AI applications.** Instrument once. Send the same telemetry to Phoenix, Langfuse, MLflow, Datadog, Arize and any OTLP backend — simultaneously — and change destinations without touching application code.

```python
from observix import observe, configure

configure(service_name="my-app", exporters=["phoenix", "langfuse"])


@observe
def my_function(): ...
```

That is the whole integration. Adding a backend, removing one, or stripping prompts before they reach a third party are all configuration changes.

---

## Why this exists

Every LLM observability backend now ingests OTLP, so *transport* is solved. What is not solved is that each backend reads a **different attribute vocabulary** and renders poorly when handed another's:

| Backend | Reads | Wants |
|---|---|---|
| Phoenix / Arize | OpenInference | `openinference.span.kind`, `input.value`, `llm.token_count.prompt` |
| Langfuse | `langfuse.*` (highest precedence) | `langfuse.observation.input`, `usage_details` |
| MLflow | `mlflow.*` | `mlflow.spanInputs`, `mlflow.llm.model` |
| Datadog / Grafana / Honeycomb | OTel GenAI | `gen_ai.*` |

Emit one vocabulary and you get a first-class experience in exactly one backend. Langfuse [#12657](https://github.com/langfuse/langfuse/issues/12657) is this problem in miniature: spans using GenAI semconv v1.37+ arrive with **null input/output**.

Meanwhile the OTel GenAI conventions are still Development-status — they moved to a [separate repository](https://github.com/open-telemetry/semantic-conventions-genai) in June 2026 — so hard-coding today's names into your application inherits every future rename.

**observix records a canonical model once and translates it per-destination at export time.** One `@observe` produces OpenInference for Phoenix, `langfuse.*` for Langfuse, and `gen_ai.*` for Datadog, all natively shaped, all from the same recording.

### What it is not

It does not reimplement OpenTelemetry. Transport, batching, retries, context propagation and sampling are OTel's. It is not another auto-instrumentation library — OpenLLMetry and OpenInference do that well, and observix [adopts their spans](docs/adopting.md) rather than competing. It is not a backend; there is no server and no UI.

---

## Install

```bash
pip install observix
```

With the backends you need:

```bash
pip install 'observix[all]'
```

---

## The developer experience

### Decorate anything

```python
from observix import observe


@observe
def handle(request): ...


@observe(kind="agent", name="planner")
async def plan(goal: str): ...  # coroutines, generators, async generators


@observe(kind="retriever", capture_output=False)
def search(query: str): ...
```

### Record AI-specific detail

```python
from observix import get_current_span

span = get_current_span()
span.record_llm_call(
    provider="anthropic",
    request_model="claude-opus-4",
    input_messages=[{"role": "user", "content": "Hello"}],
    output_messages=[{"role": "assistant", "content": "Hi"}],
    input_tokens=12,
    output_tokens=4,
)
```

Cost in USD is computed automatically from a built-in, overridable price book.

### Stream without losing the metrics that matter

When a model streams, the decorated function returns before anything is generated — so a plain decorator records an empty output and misses time-to-first-token entirely.

```python
from observix import observe, observe_stream

@observe(kind="chat")
def chat(prompt: str):
    stream = client.messages.create(..., stream=True)
    return observe_stream(stream, provider="anthropic", request_model="claude-opus-4")
```

Chunks pass through untouched. TTFT, the accumulated response and the chunk count land on the span when the stream ends — **including when it's abandoned part-way**. `observe_astream` for `async for`.

### Instrument a block

```python
from observix import observe_block

with observe_block("retrieval", kind="retriever") as span:
    docs = vector_db.search(query, k=5)
    span.set_retrieval(query=query, documents=docs, top_k=5)
```

---

## Switching backends is configuration

```toml
# observix.toml
[observix]
service_name = "my-app"
exporters = ["phoenix", "langfuse"]

[observix.exporters.langfuse]
sample_ratio = 0.25
redact = "hashed"
```

Or environment variables, for the same result with no file:

```bash
export OBSERVIX_EXPORTERS=phoenix,langfuse
export OBSERVIX_LANGFUSE_SAMPLE_RATIO=0.25
```

Or in code:

```python
configure(
    service_name="my-app",
    exporters=[
        {"provider": "phoenix"},
        {"provider": "langfuse", "sample_ratio": 0.25},
        {"provider": "datadog", "redact": "none"},  # no prompts leave the building
    ],
)
```

Precedence, lowest to highest: **defaults → config file → environment → code**.

---

## Per-destination privacy

Privacy is a property of the destination, not of the span. The same recorded prompt can go in full to your self-hosted Langfuse, hashed to staging, and not at all to a third-party SaaS:

```python
configure(
    exporters=[
        {"provider": "langfuse"},  # full content
        {"provider": "phoenix", "redact": "hashed"},  # joinable, unreadable
        {"provider": "datadog", "redact": "none"},  # metrics only
    ]
)
```

When *no* destination retains content, observix skips prompt serialisation entirely — you pay nothing for data you were never going to send.

---

## Built-in providers and dialects

**Providers** — `console`, `memory`, `otlp`, `phoenix`, `langfuse`, `mlflow`, `arize`, `datadog`
**Dialects** — `passthrough`, `otel_genai`, `openinference`, `langfuse`, `mlflow`

Add your own without forking. Declare an entry point and it becomes available by name:

```toml
[project.entry-points."observix.providers"]
mybackend = "my_pkg:MyProvider"
```

See [docs/extending.md](docs/extending.md).

---

## Testing

Spans reach the collector *after* redaction and translation, so tests assert on exactly what a backend would have received:

```python
from observix.testing import collect_spans


def test_renders_in_phoenix():
    with collect_spans(dialect="openinference") as spans:
        my_function()
    assert spans.one().attributes["openinference.span.kind"] == "LLM"
```

**And verified against a real backend.** In-memory tests prove observix emits what it intended to; they cannot prove the backend *understands* it. [`tests/live/`](tests/live/) sends real spans to a running Phoenix and asserts on its typed columns, which only populate for attributes Phoenix actually recognises. That suite caught a real bug — project routing needs a resource attribute, not the header we had been sending.

```bash
phoenix serve                       # separate terminal
pytest tests/live -m live
```

---

## Overhead

Measured, not asserted — full results and caveats in [benchmarks/](benchmarks/README.md).

| | ns/call |
|---|---:|
| Undecorated baseline | ~200 |
| `@observe`, unconfigured | ~585 |
| Raw OpenTelemetry span | ~56,000 |
| `@observe`, enabled | ~85,400 |

Unconfigured observix costs well under a microsecond. When enabled, roughly two-thirds of a span's cost is the OpenTelemetry SDK underneath it. Fan-out adds ~22 µs per extra destination on the calling thread — translation and network I/O happen on each destination's own worker. When no destination retains content, prompt serialisation is skipped entirely, cutting ~32% off a full LLM span.

---

## Documentation

- [Design and competitive analysis](docs/DESIGN.md)
- [Quickstart](docs/quickstart.md)
- [Configuration reference](docs/configuration.md)
- [Providers](docs/providers.md) · [Dialects](docs/dialects.md)
- [Extending observix](docs/extending.md)
- [Examples](examples/)

## Licence

Apache-2.0. Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
