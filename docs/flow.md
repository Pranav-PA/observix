# How execution flows through observix

A guided tour of what actually happens, in order, with file and function names so you can follow along in the source. Read this once and the codebase should stop being a maze.

There are only **three flows** worth understanding:

1. **Startup** — `configure()` builds the pipelines (once)
2. **Record** — `@observe` creates a span (per instrumented call, on your thread)
3. **Export** — redact → translate → ship (per span per destination, on a background thread)

The key mental model: **flows 2 and 3 are separated by a queue.** Your application thread does almost nothing; the expensive work happens later, elsewhere.

---

## The 30-second version

```
  configure()  ─── once ──▶  builds N independent pipelines

  @observe     ─── your thread ──▶  writes observix.* attributes  ──▶  [queue]

  [queue]      ─── background thread ──▶  redact ▶ translate ▶ HTTP  ──▶  backend
```

---

## Flow 1 — Startup: `configure()`

**Entry point:** `observix/init.py::configure()`

You call it once, early:

```python
configure(service_name="my-app", exporters=["phoenix", "langfuse"])
```

### Step 1.1 — Assemble configuration

`config.py::build_config()` merges four layers, **field by field** (never wholesale — see [decisions D15](decisions.md#d15-four-layers-merged-field-by-field)):

```
defaults
   ↓  load_config_file()      observix.toml, or [tool.observix] in pyproject.toml
   ↓  load_env_config()       OBSERVIX_* variables
   ↓  **overrides             the kwargs you just passed
ObservixConfig
```

Then `apply_exporter_env()` overlays per-destination variables like `OBSERVIX_LANGFUSE_SAMPLE_RATIO=0.25`.

The result is one `ObservixConfig` holding a list of `ExporterConfig`, one per destination.

> **Validation is loud here and only here.** A bad provider name or an out-of-range ratio raises `ConfigurationError` now, rather than silently producing no telemetry later.

### Step 1.2 — Tear down whatever was running

`init.py::_shutdown_current()` flushes and shuts down any previous provider. This is what makes `configure()` safely re-callable — important for tests.

### Step 1.3 — Build the tracer provider and pipelines

`pipeline/builder.py::build_tracer_provider()`:

```
build_resource(config)   →  service.name, service.version, deployment.environment
build_sampler(config)    →  global head sampling (ParentBased + TraceIdRatioBased)
TracerProvider(resource=..., sampler=...)
```

Then, **for each destination**, `build_pipeline()` wires a complete independent chain:

```
  ExporterConfig("langfuse", sample_ratio=0.25, redact="hashed")
        │
        ├─ resolve_provider("langfuse")   providers/registry.py  →  LangfuseProvider
        │       └─ provider.create_exporter()
        │             ├─ resolve_endpoint()   → region → https://cloud.langfuse.com/api/public/otel/v1/traces
        │             ├─ build_headers()      → Basic auth from LANGFUSE_PUBLIC_KEY / _SECRET_KEY
        │             └─ OTLPSpanExporter(...)              ◀── a plain OTel exporter
        │
        ├─ resolve_dialect("langfuse")    dialects/registry.py   →  LangfuseDialect
        │       (defaults to provider.default_dialect if not overridden)
        │
        └─ exporter_config.redaction_policy()                →  RedactionPolicy(mode=HASHED)

  ...assembled inside-out into:

  FilteringSpanProcessor(ratio=0.25)
      └─ BatchSpanProcessor              ◀── the queue + worker thread lives here
            └─ DialectSpanExporter(dialect=LangfuseDialect, redaction=HASHED)
                  └─ OTLPSpanExporter    ◀── real network I/O
```

Each pipeline is added to the `TracerProvider` via `add_span_processor()`. **N destinations = N processors = N queues = N worker threads.** A slow Langfuse cannot stall Phoenix ([D7](decisions.md#d7-one-destinations-failure-never-affects-another)).

A destination that fails to build is logged and skipped — unless *every* one fails, which raises.

### Step 1.4 — Publish the runtime

`state.py::set_runtime()` installs a `Runtime` singleton:

```python
Runtime(
    enabled=True,  # ◀── the ONE flag the hot path checks
    record_content=config.records_content(),
    tracer=provider.get_tracer("observix", __version__),
    pipelines=[...],
)
```

Two fields matter enormously for performance:

- **`enabled`** is only `True` once a tracer actually exists, so `@observe` tests exactly one boolean ([D9](decisions.md#d9-disabled-observix-costs-one-attribute-load)).
- **`record_content`** is computed **once**, here, by checking whether *any* destination's redaction policy retains content. If none does, span creation skips JSON serialisation entirely ([D10](decisions.md#d10-skip-content-serialisation-when-no-destination-will-keep-it)).

---

## Flow 2 — Record: what `@observe` does per call

**Entry point:** `observix/api.py::observe()`

### Step 2.0 — At decoration time (import time, once)

`observe()` resolves everything it can *now* so the per-call path stays thin:

```python
decorate(target):
    span_name = name or target.__qualname__
    options   = _SpanOptions(..., signature=_safe_signature(target))   # cached
    #  pick a wrapper by function shape:
    isasyncgenfunction  → _wrap_async_generator
    iscoroutinefunction → _wrap_coroutine
    isgeneratorfunction → _wrap_generator
    otherwise           → _wrap_sync
```

The signature is introspected **once**, at decoration, not on every call.

### Step 2.1 — The disabled fast path

```python
def wrapper(*args, **kwargs):
    tracer = _current_tracer()  #  runtime().tracer if runtime().enabled else None
    if tracer is None:
        return target(*args, **kwargs)  # ◀── one attribute load, then straight through
```

If you never call `configure()`, this is the entire cost of `@observe`.

### Step 2.2 — Begin the span

`api.py::_begin()`:

```
tracer.start_span(name)                        →  a real OTel span
context_api.attach(set_span_in_context(span))  →  makes it CURRENT  (contextvars)
ObservixSpan(otel_span, record_content=...)    →  the typed facade
span.set_kind(kind)                            →  writes observix.kind
_bind_arguments()                              →  writes observix.input   (if capturing)
```

The `attach()` is what makes nesting work. Because OTel context rides on **contextvars**, it survives `await` — so a span started in an `async def` is still the parent of anything called inside it, across suspension points. This is why observix needs no async-specific propagation code.

### Step 2.3 — Your function runs

Anything inside — nested `@observe` calls, `observe_block()`, `get_current_span()` — automatically attaches to this span as a child.

### Step 2.4 — Where attributes come from

Whatever your code calls on the span lands as canonical `observix.*` attributes:

```
span.record_llm_call(...)     model/span.py    →  observix.llm.request.model
                                                  observix.input.messages
                                                  observix.usage.input_tokens
                                                  ...and calls _maybe_record_cost()
                                                            ↓
                                          cost/model.py::compute_cost()
                                             normalize model name → price book lookup
                                             → observix.cost.total_usd
```

Every setter routes through `ObservixSpan._set()`, which coerces to a type OTel accepts:

```
str / bool / int / float      → stored directly
homogeneous scalar sequence   → stored as an array
anything else                 → _serde.to_json()      ◀── never raises; falls back to repr
```

Content setters go through `_set_content()`, which **returns immediately** if `record_content` is `False` — before the encoder runs.

### Step 2.5 — Finish

`api.py::_finish()`:

```
exception?  → span.record_exception()   → error.type + Status(ERROR)
otherwise   → span.set_output(result)   (if capturing)
finally:
    context_api.detach(token)   ◀── always, even on exception
    otel_span.end()             ◀── hands the span to every registered processor
```

`otel_span.end()` is the handoff point. **Flow 2 ends here**, on your thread. Everything after is Flow 3.

### The other three entry points

| Entry point | Makes span current? | You end it? | Use when |
|---|---|---|---|
| `@observe` | yes | no | decorating a function |
| `observe_block(...)` | yes | no | instrumenting a code block |
| `start_span(...)` | only if `make_current=True` | **yes** | start and end are far apart |
| `get_current_span()` | n/a | n/a | annotating whatever is already active |

`get_current_span()` never returns `None` — it returns a shared `NoOpSpan` when nothing is active, so call sites need no guards ([D19](decisions.md#d19-never-return-none-from-get_current_span)).

---

## Flow 3 — Export: redact, translate, ship

Triggered by `otel_span.end()`. The span now fans out to **every** registered processor — one per destination — and each processes it independently.

### Step 3.1 — Filter (still on your thread, but trivial)

`pipeline/filtering_processor.py::FilteringSpanProcessor.on_end()`:

```python
if ratio < 1.0 and not trace_id_ratio_keeps(trace_id, ratio):
    return  # ◀── this destination drops the span
if predicate is not None and not predicate(span):
    return
self._processor.on_end(span)  # ◀── forward to the BatchSpanProcessor
```

`trace_id_ratio_keeps()` uses the **same hash as OTel's `TraceIdRatioBased`** — upper 64 bits of the trace id compared against `ratio × 2⁶⁴`. Two consequences that matter:

- Whole **traces** are kept or dropped, never a scatter of orphaned spans.
- Two destinations at the same ratio agree on *which* traces they keep.

Filtering happens at `on_end` rather than `on_start` because predicates may need attributes that only exist once the span has run ([D12](decisions.md#d12-per-destination-sampling-needs-a-custom-processor-because-otel-genuinely-lacks-it)).

### Step 3.2 — Queue (the thread boundary)

`BatchSpanProcessor.on_end()` appends to a bounded queue and returns immediately. **Your thread is now free.**

A background worker drains the queue on a timer or when the batch fills, then calls `exporter.export(spans)`. Everything below runs there, not on your call path.

### Step 3.3 — Redact

`pipeline/translating_exporter.py::DialectSpanExporter._translate()`, first half:

```python
source = dict(span.attributes)  # canonical observix.* attributes
redacted = self._redaction.apply(source)
```

`redaction.py::RedactionPolicy.apply()` walks the attributes:

```
key matches redact_keys regex?    → "[redacted]"
not a content key?                → passed through untouched
                                    (token counts, model names, timings always survive)
content key, by mode:
    ALL        → kept  (optionally PII-scrubbed / length-capped)
    NONE       → dropped entirely
    HASHED     → "sha256:<16 hex>"     ◀── unreadable but still joinable
    TRUNCATED  → prefix + "...[truncated N chars]"
```

Two design points worth noticing:

- Redaction runs on **canonical** attributes, *before* translation. A policy is written once and behaves identically regardless of which dialect the destination uses.
- `is_content_key()` in `semconv/canonical.py` is the single source of truth for what counts as sensitive. Metrics are never redacted — you keep your latency and cost dashboards even at `redact="none"`.

### Step 3.4 — Translate

Second half of `_translate()`:

```python
view = CanonicalView(redacted, name=span.name)  # typed, lazy reader
result = self._dialect(view)  # → TranslationResult
```

`CanonicalView` (`dialects/base.py`) is a **lazy** typed reader — `view.input_messages` only parses JSON if a dialect actually asks for it.

`Dialect.__call__` runs the subclass's `translate()` and then merges foreign attributes back in with `setdefault`, so anything you set directly (`http.method`, custom keys) survives translation without clobbering dialect output.

What each dialect produces from the same recorded span:

| | `openinference` → Phoenix | `langfuse` → Langfuse | `mlflow` → MLflow | `otel_genai` → Datadog |
|---|---|---|---|---|
| kind | `openinference.span.kind=LLM` | `langfuse.observation.type=generation` | `mlflow.spanType=CHAT_MODEL` | `gen_ai.operation.name=chat` |
| input | `llm.input_messages.0.message.role` (flattened) | `langfuse.observation.input` | `mlflow.spanInputs` | `gen_ai.input.messages` + `gen_ai.prompt` |
| tokens | `llm.token_count.prompt` | `usage_details` JSON | `mlflow.chat.tokenUsage` | `gen_ai.usage.input_tokens` |
| model | `llm.model_name` | `langfuse.observation.model.name` | `mlflow.llm.model` | `gen_ai.request.model` |
| span name | unchanged | unchanged | unchanged | **renamed** to `chat claude-opus-4` |

> The `otel_genai` rename is per spec (`{operation} {model}`) and surprises people the first time. `TranslationResult.name` is how a dialect requests it.

### Step 3.5 — Rebuild and ship

```python
return rebuild_span(span, attributes=result.attributes, name=result.name)
```

`ReadableSpan.attributes` is immutable, so `rebuild_span()` constructs a **new, genuine** `ReadableSpan` — not a proxy — filtering constructor kwargs against the installed SDK's actual signature so version drift doesn't break translation ([D14](decisions.md#d14-rebuild-spans-through-readablespans-real-constructor-with-signature-filtering)).

The translated spans then go to the wrapped exporter — a stock `OTLPSpanExporter` — which handles serialisation, HTTP, compression and retry. **That is all OpenTelemetry's code**; observix does no transport work.

If translation raises, the span is exported **untranslated** rather than dropped ([D8](decisions.md#d8-never-drop-telemetry-because-translation-failed)).

---

## Worked example: one call, four backends

```python
configure(service_name="app", exporters=["phoenix", "langfuse", "mlflow", "datadog"])


@observe(kind="chat")
def call_model(prompt):
    get_current_span().record_llm_call(
        provider="anthropic",
        request_model="claude-opus-4",
        input_messages=[{"role": "user", "content": prompt}],
        input_tokens=1200,
        output_tokens=340,
    )
```

```
YOUR THREAD
  call_model("hi")
    └─ wrapper()                      api.py
        ├─ _begin()                   start_span + attach + observix.kind=chat
        ├─ record_llm_call()          observix.llm.request.model = claude-opus-4
        │                             observix.usage.input_tokens = 1200
        │                             └─ compute_cost() → observix.cost.total_usd = 0.0435
        └─ _finish()  → otel_span.end()
                             │
        ┌────────────────────┼────────────────────┬────────────────────┐
        ▼                    ▼                    ▼                    ▼
   Filter(1.0)          Filter(1.0)          Filter(1.0)         Filter(1.0)
   BatchProcessor       BatchProcessor       BatchProcessor      BatchProcessor
════════════════════ THREAD BOUNDARY — your call already returned ═════════════
        ▼                    ▼                    ▼                    ▼
   redact ALLOW_ALL     redact ALLOW_ALL     redact ALLOW_ALL    redact ALLOW_ALL
   OpenInference        Langfuse             MLflow              OTelGenAI
        ▼                    ▼                    ▼                    ▼
  openinference        langfuse.            mlflow.             gen_ai.
   .span.kind=LLM       observation          spanType=            operation
  llm.token_count       .type=generation     CHAT_MODEL           .name=chat
   .prompt=1200        usage_details        mlflow.chat          gen_ai.usage
  llm.model_name        {"input":1200}       .tokenUsage          .input_tokens
        ▼                    ▼                    ▼                    ▼
    Phoenix             Langfuse             MLflow              Datadog
```

Four backends, four native vocabularies, one `@observe`, one recording.

---

## Module map

Grouped by which flow they serve:

| Module | Flow | Role |
|---|---|---|
| `api.py` | 2 | `@observe`, `observe_block`, `start_span`, propagation helpers |
| `model/span.py` | 2 | `ObservixSpan` — the typed setter facade |
| `model/messages.py` | 2 | Normalises vendor message shapes into canonical form |
| `model/enums.py` | 2 | `SpanKind`, `Role`, `PartType`, `RedactionMode` |
| `semconv/canonical.py` | 2 | The `observix.*` vocabulary; `is_content_key()` |
| `cost/model.py` | 2 | Token → USD, with prefix-matched price lookup |
| `_serde.py` | 2 | JSON encoding that never raises |
| `state.py` | 1, 2 | The `Runtime` singleton the hot path reads |
| `init.py` | 1 | `configure()`, `flush()`, `shutdown()` |
| `config.py` | 1 | Four-layer config assembly |
| `pipeline/builder.py` | 1 | Wires provider + dialect + redaction into a `Pipeline` |
| `providers/*` | 1 | Backend presets: endpoint, auth, default dialect |
| `_registry.py` | 1 | Entry-point plugin discovery |
| `pipeline/filtering_processor.py` | 3 | Per-destination sampling |
| `pipeline/translating_exporter.py` | 3 | Redact → translate → rebuild → delegate |
| `redaction.py` | 3 | `RedactionPolicy` |
| `dialects/base.py` | 3 | `CanonicalView`, `Dialect`, `TranslationResult` |
| `dialects/*.py` | 3 | One translation per backend vocabulary |
| `semconv/{genai,openinference,langfuse,mlflow}.py` | 3 | Target vocabularies as constants |
| `testing.py` | — | `collect_spans`, `multi_collector` |

---

## Reading the code in a sensible order

1. **`semconv/canonical.py`** — the vocabulary everything else moves around
2. **`api.py::observe`** → **`_begin`** / **`_finish`** — how a span starts and ends
3. **`model/span.py::ObservixSpan._set`** — how a value becomes an attribute
4. **`dialects/base.py`** — `CanonicalView` and `Dialect`, the translation contract
5. **`dialects/openinference.py`** — the most involved dialect; read it once and the rest are obvious
6. **`pipeline/translating_exporter.py`** — where redaction and translation meet
7. **`pipeline/builder.py`** — how a config line becomes a running pipeline

---

## Three invariants to hold onto

1. **The application thread only ever writes canonical attributes and enqueues.** Redaction, translation and I/O happen on a background thread. If you are adding work, know which side of the queue it lands on.

2. **`observix.*` never reaches a backend** unless the `passthrough` dialect is explicitly selected. Dialects that lack a home for a value (cost in `gen_ai`, say) deliberately keep the canonical key rather than invent a name — so data is never silently lost.

3. **Nothing in flows 2 or 3 may raise into user code.** `suppress_and_log` guards the boundaries; `OBSERVIX_STRICT=1` turns that off for debugging and is what our own test suite runs with.
