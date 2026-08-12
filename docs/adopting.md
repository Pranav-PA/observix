# Adopting spans from other instrumentation libraries

If your application is already instrumented with **OpenLLMetry**, **OpenInference** or **OpenLIT**, you do not have to re-instrument it to get observix's multi-backend fan-out and per-destination privacy.

```python
configure(
    service_name="my-app",
    exporters=["phoenix", "langfuse", "datadog"],
    adopt_foreign=True,
)
```

Inbound spans in another vocabulary are mapped onto observix's canonical model, then flow through the same dialect pipeline as `@observe` spans.

## What this buys you

An app instrumented with OpenLLMetry emits `gen_ai.*`. Phoenix will show those spans, but without OpenInference attributes it cannot render span kinds, token counts or per-message content properly. With adoption on:

```
  OpenLLMetry span (gen_ai.*)
        ↓  normalize_foreign_attributes()
  canonical observix.*
        ↓  fan out
  ┌─────────────┬──────────────┬─────────────┐
  openinference    langfuse      otel_genai
  → Phoenix      → Langfuse     → Datadog
```

Each backend now receives its own vocabulary, and each destination's redaction policy applies to the adopted content too.

## What is recognised

| Source | Attributes mapped |
|---|---|
| **OTel GenAI** | `gen_ai.operation.name`, `gen_ai.request.*`, `gen_ai.response.*`, `gen_ai.usage.*`, `gen_ai.prompt` / `gen_ai.completion`, `gen_ai.input.messages` / `output.messages`, `gen_ai.tool.*`, `gen_ai.conversation.id`, and the pre-1.36 `gen_ai.system` |
| **OpenInference** | `openinference.span.kind`, `input.value` / `output.value`, `llm.model_name`, `llm.provider`, `llm.token_count.*`, `llm.cost.*`, `session.id`, `user.id`, `tool.*`, and the flattened `llm.input_messages.N.message.*` keys |
| **MLflow** | `mlflow.spanType`, `mlflow.spanInputs` / `spanOutputs`, `mlflow.llm.*`, `mlflow.chat.tokenUsage`, `mlflow.trace.session` / `user` |
| **Traceloop** | `traceloop.entity.name`, `traceloop.workflow.name` |

Span kind is inferred from `openinference.span.kind`, then `gen_ai.operation.name`, then `mlflow.spanType`. Failing all three, a span carrying token counts is treated as a `chat`.

OpenInference's flattened indexed message keys are reassembled into canonical messages, so content survives the round trip.

## Guarantees

**Conservative.** A span already carrying any `observix.*` attribute is left completely alone — observix never re-derives its own output.

**Additive only.** Foreign attributes are preserved, so a destination that already understood them keeps working. An existing canonical value is never overwritten.

**Redaction still applies.** Normalisation runs *before* redaction, so adopted prompts obey each destination's privacy policy exactly as your own do.

**Off by default.** Adoption costs an attribute scan per span per destination. Enable it only when you have foreign spans to adopt.

## Per-destination

Adopt at one destination and not another:

```python
configure(
    exporters=[
        {"provider": "phoenix", "options": {"adopt_foreign": True}},
        {"provider": "otlp", "name": "raw"},  # receives spans untouched
    ]
)
```

## Using the mapping directly

```python
from observix.integrations.adopt import (
    looks_foreign,
    infer_kind,
    normalize_foreign_attributes,
)

if looks_foreign(attributes):
    canonical = normalize_foreign_attributes(attributes)
```

## Limitations

- **Lossy where the source is lossy.** A backend that only recorded `input.value` as flat text cannot be turned back into structured messages.
- **Auto-instrumentation stays theirs.** Adoption reshapes spans other libraries emit; it does not patch vendor SDKs. Keep using OpenLLMetry or OpenInference for that — [that is deliberate](DESIGN.md#14-explicit-non-goals--what-we-refuse-to-reinvent).
- **Vocabulary drift.** The mapping tracks the conventions as they are today. New attributes appear; open an issue if you hit one that is missing.

## Mixing adopted and native spans

They compose. Both end up canonical before translation, so a trace containing an OpenLLMetry span and an `@observe` span renders consistently in every destination.

```python
configure(exporters=["phoenix", "langfuse"], adopt_foreign=True)


@observe(kind="workflow")  # observix-native
def pipeline(q):
    return openai_client.chat(...)  # OpenLLMetry-instrumented, adopted
```
