# observix — Technical Design

> One instrumentation API. Every observability backend. No collector required.

Status: **living document**. Version 0.1.x (pre-stable).

---

## 1. Competitive analysis and the gap

### 1.1 What already exists

| Project | What it actually is | Vocabulary emitted | Multi-backend? |
|---|---|---|---|
| **OpenTelemetry SDK** | Transport + context propagation + sampling + batching | `gen_ai.*` (GenAI semconv) | Yes, via Collector |
| **OpenLLMetry** (Traceloop) | Auto-instrumentation of vendor SDKs | `gen_ai.*` + `traceloop.*` | One exporter per SDK init |
| **OpenLIT** | Auto-instrumentation + self-hosted UI | `gen_ai.*` | Single OTLP target |
| **OpenInference** (Arize) | Auto-instrumentation, Phoenix-native | `openinference.*`, `input.value`, `llm.*` | Single OTLP target |
| **Langfuse** | Backend/platform + SDK | `langfuse.*` (highest precedence) | N/A — it is a destination |
| **Phoenix** | Backend/platform (OSS) | consumes OpenInference | N/A — it is a destination |
| **MLflow Tracing** | Backend + lifecycle platform | `mlflow.span*` | N/A — it is a destination |

### 1.2 The three facts that define the gap

**Fact 1 — Transport is solved; vocabulary is not.**
Every major backend now exposes an OTLP endpoint. Getting *bytes* to Langfuse, Phoenix, Datadog or MLflow is a solved problem. What is not solved is that each backend reads a *different attribute vocabulary* and degrades when given another's:

- Langfuse documents that `langfuse.*` attributes take **highest precedence**, with `gen_ai.*` / `input.value` / `mlflow.spanInputs` as lossy fallbacks.
- Phoenix renders span kind, token counts and I/O from `openinference.span.kind`, `llm.token_count.prompt`, `input.value`.
- MLflow maps *both* families inward to `mlflow.spanInputs` / `mlflow.llm.model`.
- Langfuse [#12657](https://github.com/langfuse/langfuse/issues/12657): spans using GenAI semconv v1.37+ (events-based content) arrive with **null input/output** — data present in metadata, invisible in the UI.

So "just emit OTel gen_ai" produces a first-class experience in *one* backend and a second-class one in the rest.

**Fact 2 — The GenAI semconv is a moving target.**
As of semantic-conventions v1.42.0 (June 2026) the GenAI conventions were deprecated out of the main repo into `open-telemetry/semantic-conventions-genai`, which still carries `Status: Development` with no tagged release. Applications that hard-code today's attribute names inherit every future rename.

**Fact 3 — In-process fan-out is a known missing feature.**
`traceloop-sdk` supports a single OTLP endpoint; there is an open feature request for multiple endpoints, and the current guidance is to deploy an OpenTelemetry Collector. A Collector is correct at platform scale and disproportionate for a library user who wants prompts in self-hosted Langfuse and latency in Datadog.

### 1.3 The gap observix fills

> **A canonical, AI-aware telemetry model recorded once, translated per-destination at export time, fanned out to many backends in-process, with independent privacy and sampling policy per destination.**

Concretely, four things nobody else does together:

1. **Dialect translation at export.** One recorded span → Phoenix sees OpenInference, Langfuse sees `langfuse.*`, Datadog sees `gen_ai.*`, MLflow sees `mlflow.*`. All native. All from one `@observe`.
2. **In-process multi-destination fan-out** with per-destination exporters, no Collector.
3. **Per-destination redaction.** Send full prompts to your self-hosted Langfuse; strip them before they reach a third-party SaaS. Privacy policy is a property of the *destination*, not the span.
4. **Semconv insulation.** Your application code names nothing from a Development-status spec. When `gen_ai.*` renames a field, one dialect changes; your code does not.

### 1.4 Explicit non-goals — what we refuse to reinvent

- ❌ **Transport, batching, retry/backoff, compression** → `BatchSpanProcessor` + OTLP exporters.
- ❌ **Context propagation, W3C `traceparent`, contextvars** → OTel API.
- ❌ **Sampling primitives** → OTel `Sampler`s (we add per-destination *filtering*, which OTel genuinely lacks).
- ❌ **Auto-instrumenting every vendor SDK** → OpenLLMetry / OpenInference do this well. We *adopt* their spans instead (§7).
- ❌ **Being a backend or a UI.** We have no server, no database, no dashboard.

observix is a thin, opinionated layer *above* OTel and *beside* the instrumentation libraries — never a replacement for either.

---

## 2. Architecture

```
  Application code
       │  @observe / observe_block() / start_span()
       ▼
  ┌─────────────────────────────────────────────┐
  │  observix API           (provider-agnostic) │
  │  ObservixSpan facade — typed setters        │
  └─────────────────────────────────────────────┘
       │  writes canonical attributes: observix.*
       ▼
  ┌─────────────────────────────────────────────┐
  │  OpenTelemetry SDK                          │
  │  real spans · contextvars · sampling · W3C  │
  └─────────────────────────────────────────────┘
       │  ReadableSpan (observix.* vocabulary)
       ├──────────────┬──────────────┬───────────────┐
       ▼              ▼              ▼               ▼
   Pipeline A     Pipeline B     Pipeline C      Pipeline D
   filter+ratio   filter         filter          filter
   redact:none    redact:none    redact:hashed   redact:all
   dialect:       dialect:       dialect:        dialect:
    openinference  langfuse       otel_genai      mlflow
       │              │              │               │
   BatchSpanProcessor (one per pipeline — independent queues)
       │              │              │               │
   OTLP/http      OTLP/http      OTLP/http       OTLP/http
       ▼              ▼              ▼               ▼
    Phoenix       Langfuse        Datadog         MLflow
```

**The load-bearing decision: record canonical, translate at export.**

Spans are recorded once but exported N times. Translating at *creation* forces a single vocabulary. Translating at *export* lets every destination receive its own native dialect from one recording — and makes the dialect a pure, independently testable function.

`ReadableSpan.attributes` is immutable, so `DialectSpanExporter` constructs a *new* `ReadableSpan` carrying translated attributes and delegates to the wrapped exporter. Because the result is a real `ReadableSpan`, third-party exporters that type-check continue to work.

---

## 3. The canonical model

Namespace: **`observix.*`** — stable, versioned by us, never emitted to a backend unless you explicitly choose the `passthrough` dialect.

### Span kinds
`llm` · `chat` · `embedding` · `tool` · `agent` · `workflow` · `chain` · `retriever` · `reranker` · `guardrail` · `task` · `unknown`

### Attribute families

| Family | Keys |
|---|---|
| Core | `observix.kind`, `observix.name`, `observix.input`, `observix.output` |
| Messages | `observix.input.messages`, `observix.output.messages`, `observix.system_instructions` |
| LLM request | `observix.llm.provider`, `.request.model`, `.request.temperature`, `.top_p`, `.top_k`, `.max_tokens`, `.stop_sequences`, `.frequency_penalty`, `.presence_penalty`, `.seed` |
| LLM response | `observix.llm.response.model`, `.response.id`, `.response.finish_reasons`, `.streaming`, `.time_to_first_token_ms` |
| Usage | `observix.usage.input_tokens`, `.output_tokens`, `.total_tokens`, `.reasoning_tokens`, `.cache_read_input_tokens`, `.cache_write_input_tokens` |
| Cost | `observix.cost.input_usd`, `.output_usd`, `.total_usd` |
| Tool | `observix.tool.name`, `.description`, `.call_id`, `.arguments`, `.result` |
| Retrieval | `observix.retrieval.query`, `.documents`, `.top_k` |
| Identity | `observix.session.id`, `observix.user.id`, `observix.conversation.id`, `observix.tags` |
| Prompt mgmt | `observix.prompt.name`, `observix.prompt.version` |
| Free-form | `observix.metadata.<key>` |

Errors reuse OTel: span `Status` + `error.type`.

### Canonical message shape

Forward-compatible with `gen_ai.input.messages`; trivially down-convertible to OpenInference's flattened indexed form.

```jsonc
[{"role": "user", "parts": [{"type": "text", "content": "Hello"}]}]
```

Part types: `text` · `tool_call` · `tool_call_response` · `reasoning` · `blob`.

---

## 4. Public API design

```python
from observix import observe, observe_block, configure, get_current_span

configure(exporters=["phoenix", "langfuse"], service_name="my-app")


@observe  # bare
def plain(): ...


@observe(kind="agent", name="planner")  # parameterised
async def plan(goal: str): ...  # async, generators, async generators


with observe_block("retrieval", kind="retriever") as span:
    span.set_retrieval(query=q, documents=docs, top_k=5)

span = get_current_span()
span.record_llm_call(
    provider="anthropic", request_model="claude-opus-4", input_messages=msgs, output_messages=out
)
span.set_usage(input_tokens=1200, output_tokens=340)
span.set_session(user_id="u_1", session_id="s_9")
span.set_metadata(experiment="b")
```

Backend switching is configuration only:

```toml
# observix.toml
[observix]
service_name = "my-app"
exporters = ["phoenix", "langfuse"]

[observix.exporters.langfuse]
redact = "hashed"
sample_ratio = 0.25
```

or `OBSERVIX_EXPORTERS=phoenix,langfuse`. Application code never changes.

---

## 5. Plugin architecture

Two independent registries, both extensible via entry points — contributors add backends **without touching core**:

```toml
[project.entry-points."observix.providers"]
mybackend = "my_pkg:MyProvider"

[project.entry-points."observix.dialects"]
mydialect = "my_pkg:MyDialect"
```

**Provider** = a destination preset: default endpoint, protocol, auth header construction, default dialect, env-var conventions.
**Dialect** = a pure function `CanonicalView -> TranslationResult`.

Built-in providers: `console`, `memory`, `otlp`, `phoenix`, `langfuse`, `mlflow`, `arize`, `datadog`.
Built-in dialects: `passthrough`, `otel_genai`, `openinference`, `langfuse`, `mlflow`.

---

## 6. Cross-cutting concerns

| Concern | Approach |
|---|---|
| **Async** | `@observe` detects coroutine / async-gen / sync-gen / sync and wraps each correctly. Context flows through contextvars. |
| **Propagation** | OTel's `TraceContextTextMapPropagator`; `observix.propagation` exposes `inject`/`extract` helpers. |
| **Sampling** | Global OTel `Sampler` + per-destination `FilteringSpanProcessor` with trace-id-consistent ratio (same hash as `TraceIdRatioBased`, so decisions agree across destinations). |
| **Batching / retries** | Delegated to `BatchSpanProcessor` and OTLP exporters. One queue per destination — a slow backend cannot block another. |
| **Failure handling** | Every observix boundary is fail-open: instrumentation errors are caught, counted, logged once at WARNING, and never propagate into user code. |
| **Overhead** | Disabled → one attribute load per call. Content serialisation is lazy and skipped entirely when no destination retains content. |
| **Typing** | `py.typed`, strict mypy, `ParamSpec`-preserving decorator signatures. |

---

## 7. Adopting foreign spans

`observix.integrations.adopt` re-dialects spans emitted by OpenLLMetry / OpenInference / OpenLIT so an existing instrumented app gains multi-backend fan-out with no re-instrumentation:

```python
from observix.integrations.adopt import adopt_foreign_spans

adopt_foreign_spans()  # inbound gen_ai.*/openinference.* → canonical → all dialects
```

---

## 8. Repository structure

```
observix/
├── pyproject.toml · README.md · LICENSE · CHANGELOG.md · CONTRIBUTING.md
├── docs/          DESIGN.md, quickstart, providers, dialects, extending
├── examples/      quickstart, multi-backend, async, custom provider
├── tests/         unit + dialect golden tests + integration
└── src/observix/
    ├── api.py config.py init.py state.py errors.py types.py testing.py
    ├── semconv/   canonical · genai · openinference · langfuse · mlflow
    ├── model/     enums · messages · usage · span
    ├── dialects/  base · registry · passthrough · otel_genai · openinference · langfuse · mlflow
    ├── pipeline/  translating_exporter · filtering_processor · redaction · builder
    ├── providers/ base · registry · console · memory · otlp · phoenix · langfuse · mlflow · arize · datadog
    ├── cost/      model · prices
    └── integrations/ adopt
```

---

## 9. MVP roadmap

| Milestone | Contents | State |
|---|---|---|
| **M0** Foundation | packaging, typing, errors, config, state, canonical semconv | ✅ |
| **M1** Core API | `@observe` (sync/async/gen), `ObservixSpan`, `console`/`memory` providers | ✅ |
| **M2** Dialects | dialect engine, translating exporter, 5 built-in dialects | ✅ |
| **M3** Providers | registry, OTLP presets, multi-destination fan-out, entry points | ✅ |
| **M4** Policy | redaction, per-destination sampling, cost model | ✅ |
| **M5** Ecosystem | adopt foreign spans, docs, examples, PyPI release | ◐ |
| **M6** Beyond | metrics + logs signals, eval/feedback hooks, more providers | ○ |

---

## 10. Design principles

1. **Never reinvent OpenTelemetry.** If OTel does it, delegate.
2. **Fail open.** Observability must never take down the application.
3. **Canonical in, native out.** Applications speak our vocabulary; backends receive theirs.
4. **Policy belongs to the destination.** Redaction and sampling are per-exporter.
5. **Extensible without forking.** New backends are entry points, not core edits.
6. **Zero-config is a no-op.** No configuration means near-zero overhead.
