# Dialects

A **dialect** translates observix's canonical model into one backend's native attribute vocabulary. Translation happens at **export** time, which is what lets a single recorded span arrive natively-shaped at several backends at once.

Dialects are pure functions: `CanonicalView → TranslationResult`. No I/O, no span lifecycle.

## The same span, five ways

Recorded once:

```python
span.record_llm_call(
    provider="anthropic",
    request_model="claude-opus-4",
    input_messages=[{"role": "user", "content": "Hello"}],
    input_tokens=1200,
    output_tokens=340,
)
```

| | `passthrough` | `otel_genai` | `openinference` | `langfuse` | `mlflow` |
|---|---|---|---|---|---|
| **Target** | debugging | Datadog, Grafana, Honeycomb | Phoenix, Arize | Langfuse | MLflow |
| **Kind** | `observix.kind=chat` | `gen_ai.operation.name=chat` | `openinference.span.kind=LLM` | `langfuse.observation.type=generation` | `mlflow.spanType=CHAT_MODEL` |
| **Model** | `observix.llm.request.model` | `gen_ai.request.model` | `llm.model_name` | `langfuse.observation.model.name` | `mlflow.llm.model` |
| **Input** | `observix.input.messages` | `gen_ai.input.messages` + `gen_ai.prompt` | `llm.input_messages.0.message.role` … | `langfuse.observation.input` | `mlflow.spanInputs` |
| **Tokens** | `observix.usage.input_tokens` | `gen_ai.usage.input_tokens` | `llm.token_count.prompt` | `usage_details` JSON | `mlflow.chat.tokenUsage` JSON |
| **Cost** | `observix.cost.total_usd` | *(kept canonical)* | `llm.cost.total` | `cost_details` JSON | *(kept canonical)* |
| **Span name** | unchanged | **renamed** `chat claude-opus-4` | unchanged | unchanged | unchanged |

Run `python examples/02_multi_backend.py` to see all four side by side.

---

## `otel_genai`

Targets the OpenTelemetry GenAI semantic conventions. The default for generic OTLP backends.

**One deliberate deviation from a strict reading of the spec:** structured `gen_ai.input.messages` is emitted *alongside* the legacy flat `gen_ai.prompt` / `gen_ai.completion`. Several backends still only read the flat form, and emitting both is the difference between visible content and a blank panel. Disable with `dialect_options={"legacy_content": False}`.

**Span renaming.** The spec names spans `{operation} {model}` — so `call_model` becomes `chat claude-opus-4`. Surprising the first time; correct per spec.

**Cost and session have no `gen_ai.*` home.** Rather than invent names, the dialect keeps the canonical keys so nothing is silently lost.

---

## `openinference`

Targets Arize Phoenix and Arize AX.

**Messages are flattened into indexed keys** — `llm.input_messages.0.message.role`, `.0.message.content`, `.1.message.role`, … A single JSON blob would render but could not be inspected message by message, which is most of what Phoenix's trace view is for.

Retrieved documents flatten the same way (`retrieval.documents.0.document.content`), as do tool calls within messages.

Request parameters collapse into one `llm.invocation_parameters` JSON blob, which is OpenInference's shape.

`max_messages` (default 128) bounds the flattening so a pathological conversation cannot produce thousands of attributes.

---

## `langfuse`

Targets Langfuse.

Langfuse documents that `langfuse.*` takes **highest precedence**, falling back to inferring from `gen_ai.*` / OpenInference / MLflow. Writing the namespace explicitly is what avoids [langfuse#12657](https://github.com/langfuse/langfuse/issues/12657), where GenAI-semconv v1.37+ spans arrive with null input/output.

Usage and cost go over as `usage_details` / `cost_details` JSON objects, which is what Langfuse's ingestion expects.

Span kinds map onto Langfuse observation types: `chat`/`llm` → `generation`, `tool` → `tool`, `retriever` → `retriever`, and so on. Error spans also set `langfuse.observation.level=ERROR`.

Constructor options: `release`, `environment`.

---

## `mlflow`

Targets MLflow tracing. `mlflow.spanInputs` / `spanOutputs` must parse as JSON, so plain strings are wrapped rather than passed through raw.

Cost goes to MLflow's native `mlflow.llm.cost` object. MLflow populates that field itself, but only for models in its own price table — so emitting it explicitly is what makes cost visible for fine-tunes, private models, and anything priced from a [custom price book](configuration.md#cost). Verified against MLflow 3.x, where an unknown model produced no cost attribute at all.

---

## `passthrough`

The identity dialect — emits canonical `observix.*` attributes unchanged. Useful for debugging, for backends you control, and as the baseline in dialect tests. This is the only way `observix.*` ever reaches a backend.

---

## Choosing a dialect

Each provider has a sensible default. Override per destination when you need to:

```python
configure(
    exporters=[
        {
            "provider": "otlp",
            "name": "self-hosted-phoenix",
            "endpoint": "http://phoenix:6006",
            "dialect": "openinference",
        },
        {"provider": "console", "dialect": "langfuse"},  # preview what Langfuse sees
    ]
)
```

Pass constructor options through `dialect_options`:

```python
{"provider": "datadog", "options": {"dialect_options": {"legacy_content": False}}}
```

## Foreign attributes always survive

Anything you set directly — `http.method`, your own custom keys — passes through every dialect untouched. Dialect output takes precedence on collision.

## Writing your own

```python
from observix.dialects import Dialect, CanonicalView, TranslationResult, register_dialect


class AcmeDialect(Dialect):
    name = "acme"

    def __init__(self, *, capture_content: bool = True) -> None:
        self.capture_content = capture_content

    def translate(self, view: CanonicalView) -> TranslationResult:
        result = TranslationResult()
        result.set("acme.op", view.kind.value)
        result.set("acme.model", view.model)
        result.set("acme.tokens.in", view.usage.input_tokens)
        if self.capture_content:
            result.set("acme.prompt", view.input_text())
        return result


register_dialect("acme", AcmeDialect)
```

`TranslationResult.set()` ignores `None`, so absent data stays absent — OTel rejects `None` attributes.

Accept `capture_content: bool = True` so the global `capture_content=False` switch works. Set `result.name` to rename the span.

See [`CanonicalView`](../src/observix/dialects/base.py) for the full typed reader, and `examples/05_custom_provider.py` for a runnable version.
