# Decision log

Every meaningful design decision in observix, with the reasoning, the alternatives that were rejected, and the cost we accepted. Newest sections build on earlier ones, so it reads top to bottom.

Format per entry: **what we decided → why → what we rejected → what it costs us.**

---

## 1. Positioning

### D1. observix is a translation and fan-out layer, not an instrumentation library or a backend

**Decision.** Build the layer that sits *above* OpenTelemetry and *beside* OpenLLMetry/OpenInference: canonical model in, N backend-native vocabularies out.

**Why.** The research pass established three facts:

1. **Transport is solved.** Phoenix, Langfuse, MLflow, Arize and Datadog all ingest OTLP. Nobody needs another way to move bytes.
2. **Vocabulary is not solved.** Each backend reads a different attribute namespace and treats its own as highest-precedence. Langfuse [#12657](https://github.com/langfuse/langfuse/issues/12657) — GenAI-semconv v1.37+ spans arriving with null input/output — is this exact failure in the wild.
3. **In-process fan-out is a known gap.** `traceloop-sdk` supports one OTLP endpoint; the documented multi-backend answer is "deploy an OpenTelemetry Collector," which is disproportionate for a library user who wants prompts in self-hosted Langfuse and latency in Datadog.

The intersection of those three is a real, unoccupied niche.

**Rejected.**
- *Another auto-instrumentation library.* OpenLLMetry and OpenInference already patch the vendor SDKs well. Competing there means permanent catch-up on other people's API churn for no differentiation.
- *A backend with a UI.* Enormously more work, and it would make us a competitor to the very systems we want to export to.
- *A thin OTel wrapper.* No value over using OTel directly.

**Cost.** We depend on the ecosystem's vocabularies staying roughly stable, and we own a mapping table that needs maintenance as backends evolve. Accepted: that maintenance is exactly the value we provide — it is one library's problem instead of every application's.

---

### D2. Never reinvent what OpenTelemetry provides

**Decision.** Transport, batching, retry/backoff, compression, context propagation, W3C `traceparent`, sampling primitives and span lifecycle are all delegated to the OTel SDK. observix configures them; it does not reimplement them.

**Why.** These are solved, battle-tested, and carry deep subtle correctness requirements (backpressure, shutdown ordering, contextvar semantics across `await`). Reimplementing them would be strictly worse and would fragment the ecosystem.

**Rejected.** A custom transport with its own queue. Considered briefly for the "no OTel dependency" pitch — rejected because it would have made every existing OTel-instrumented library invisible to us.

**Cost.** We inherit OTel's API surface and its version churn. Mitigated in D14.

---

## 2. The core architectural bet

### D3. Record canonical attributes; translate at **export** time, not at span creation

**Decision.** `@observe` and the span setters write `observix.*` attributes. Translation to `gen_ai.*` / OpenInference / `langfuse.*` / `mlflow.*` happens inside each destination's exporter.

**Why.** This single decision is what makes the whole product work. Spans are recorded **once** but exported **N times**. Translating at creation forces one vocabulary and reduces you to what everyone else already does. Translating at export lets one recording arrive natively-shaped at every destination simultaneously.

It also makes dialects *pure functions* — no I/O, no span lifecycle, trivially unit-testable, and swappable per destination without touching the hot path.

**Rejected.**
- *Translate at creation.* Cheaper (one pass, not N) but structurally incapable of multi-backend fidelity. This is the whole differentiator; giving it up leaves nothing.
- *Emit all vocabularies at once on every span.* Considered — "just write `gen_ai.*` AND `input.value` AND `langfuse.*`." Rejected: it triples span size on the wire for every destination, and backends that see a foreign namespace they partially understand render *worse*, not better, than one they don't see at all.

**Cost.** Translation runs per span per destination, on the exporter's background thread. Measured as negligible relative to the network I/O it precedes, and critically it is **off the application's call path** — the `BatchSpanProcessor` worker absorbs it.

---

### D4. Own a canonical namespace (`observix.*`) rather than adopting `gen_ai.*` internally

**Decision.** Define our own attribute vocabulary as the internal representation.

**Why.** The OTel GenAI conventions were deprecated out of the main semconv repo into [`semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai) in v1.42.0 (June 2026) and still carry `Status: Development` with no tagged release. Applications that hard-code today's names inherit every future rename.

With a canonical namespace, an upstream rename touches exactly one file — `dialects/otel_genai.py`. Application code never moves.

**Rejected.** *Use `gen_ai.*` as the internal model.* Tempting (no translation needed for the most common case) but it welds our public API to a moving Development-status spec, and it makes OpenInference the awkward translation instead of one peer among several.

**Cost.** One more vocabulary in the world. Mitigated by never emitting it — `observix.*` reaches a backend only via the explicit `passthrough` dialect.

---

### D5. Canonical messages mirror `gen_ai.input.messages`, not OpenInference's flattened form

**Decision.** `[{role, parts: [{type, content}]}]` as the internal message shape.

**Why.** Down-converting rich structure to a flat form is lossless; the reverse is not. The `{role, parts}` shape is the richest of the target vocabularies, so it can produce OpenInference's `llm.input_messages.0.message.role`, Langfuse's flat `input`, and MLflow's `spanInputs` without inventing data.

**Cost.** More parsing work for the OpenInference dialect (it must flatten). Correct direction to pay it in.

---

## 3. Reliability

### D6. Fail open, everywhere, with one deliberate exception

**Decision.** Every boundary between user code and observix internals is wrapped in `suppress_and_log`. Instrumentation errors are caught, logged once per call-site, and never propagate. **Configuration errors are the exception** — they raise, loudly, at `configure()` time.

**Why.** Observability that takes down the application it observes is worse than no observability. But the failure modes split cleanly:

- A malformed prompt object at runtime → suppress. The application must not care.
- A typo in `exporters=["phenix"]` → raise. It is a programmer error, cheap to fix, and silently swallowing it means the user ships thinking they have telemetry when they have none.

**Supporting detail.** Log deduplication by call-site means a hot loop that fails on every call logs once at WARNING, then DEBUG. Without this, a bad object in a request handler floods the log at request rate.

**Escape hatch.** `OBSERVIX_STRICT=1` re-raises everything — used by our own test suite and by anyone debugging a custom plugin. Suppression that cannot be turned off is undebuggable.

---

### D7. One destination's failure never affects another

**Decision.** Each destination gets its own `BatchSpanProcessor` — its own queue, its own worker thread. A destination that fails to *build* is skipped with an error log rather than aborting the rest.

**Why.** The entire premise is "send to several backends at once." If a Langfuse outage stalls your Datadog export, the feature is a liability. Independent queues make backpressure local.

**Nuance.** If *every* destination fails to build we raise, surfacing the first error. Skipping silently when nothing works would leave the user with a completely dead pipeline and no signal.

**Cost.** N worker threads and N queues instead of one shared queue. Accepted — thread-per-destination is a handful of threads, and shared queueing would couple exactly what we are trying to decouple.

---

### D8. Never drop telemetry because translation failed

**Decision.** If a dialect raises, `DialectSpanExporter` exports the span **untranslated** rather than dropping it.

**Why.** A degraded span in a backend is recoverable — a human can read `observix.*` attributes. A missing span is not; it is invisible, and the failure is silent.

---

## 4. Performance

### D9. Disabled observix costs one attribute load

**Decision.** `@observe` checks `runtime().enabled` and, when false, calls the target function directly. The runtime is a module-level singleton, not a registry lookup or a contextvar.

**Why.** The decorator runs on every instrumented call. Anything more than a single attribute load is a tax on users who have not configured anything — and "zero-config is a no-op" is a stated principle.

**Invariant that makes it work.** `Runtime.enabled` is only `True` once a tracer actually exists. That way the hot path tests **one** flag rather than `enabled and tracer is not None and pipelines`.

**Rejected.** *Returning the original function unwrapped when disabled at import time.* Faster still, but breaks the extremely common pattern of decorating at import and calling `configure()` later in `main()`.

---

### D10. Skip content serialisation when no destination will keep it

**Decision.** `ObservixSpan._set_content` checks `record_content` **before** running the JSON encoder. The flag is computed once at `configure()` time by `ObservixConfig.records_content()`.

**Why.** Serialising a large prompt only to have every destination's redaction policy discard it is pure waste. Deciding once, at configuration time, means a fully-redacted deployment pays nothing for data it was never going to send.

---

## 5. Policy

### D11. Redaction is a property of the destination, not of the span

**Decision.** Each `ExporterConfig` carries its own `RedactionPolicy`, applied inside that destination's exporter.

**Why.** This is a genuine capability nobody else offers, and it falls out naturally from D3. The real-world need is concrete: full prompts to your self-hosted Langfuse, hashed to a shared staging backend, none at all to a third-party SaaS — from one recording.

Span-level redaction cannot express this, because at record time you do not know which destination a span is headed for. It is headed for all of them.

**Detail.** Redaction runs on **canonical** attributes, before translation. So a policy is written once and behaves identically no matter which dialect the destination uses. Writing policies against per-backend attribute names would mean rewriting them per destination.

**Modes.** `all` / `none` / `hashed` / `truncated`. `hashed` exists specifically to preserve joinability — you can still tell that two requests had the same prompt without being able to read it.

**Explicit limitation.** The PII detectors are regexes for the common accidental leaks. They are documented as *not* a compliance control. Claiming otherwise would be dishonest and dangerous.

---

### D12. Per-destination sampling needs a custom processor, because OTel genuinely lacks it

**Decision.** `FilteringSpanProcessor` wraps each destination's batch processor and applies a per-destination ratio.

**Why.** OTel samples once, per `TracerProvider`. That is right for one backend and wrong for "everything in my own Langfuse, 5% in a metered SaaS." The alternatives were a second `TracerProvider` (duplicated spans, broken parent-child relationships) or a Collector (D1's whole point is avoiding that).

**Correctness detail.** Ratio sampling reuses the **same trace-id hash as `TraceIdRatioBased`** — the upper 64 bits compared against `ratio × 2⁶⁴`. This matters twice over: whole traces are kept rather than a scatter of orphaned spans, and two destinations at the same ratio agree on *which* traces they keep.

**Why the decision happens at `on_end`, not `on_start`.** Predicates may need attributes that are only populated during the span's life. Spans are forwarded to `on_start` unconditionally and filtered at `on_end`.

**This is the one place we add a sampling primitive** rather than delegating — justified because the gap is real, not because we prefer our own.

---

## 6. Extensibility

### D13. Two registries, entry-point backed, with explicit registration winning

**Decision.** `observix.providers` and `observix.dialects` entry-point groups. Contributors add backends without touching core. Explicit `register_provider()` calls override discovered ones.

**Why.** "Extensible without forking" is a principle. The precedence rule (explicit > discovered) lets a user override a built-in — e.g. patch the Langfuse provider for a private deployment — without publishing a package.

**Detail.** Built-ins are *also* registered eagerly in code, not only via entry points, so observix works from a source checkout where entry points may not be installed.

**Failure handling.** A plugin that fails to import is logged and skipped. One broken third-party package must not prevent the rest of the pipeline from starting.

---

### D14. Rebuild spans through `ReadableSpan`'s real constructor, with signature filtering

**Decision.** `rebuild_span()` constructs a genuine `ReadableSpan` and filters kwargs against the installed SDK's actual `__init__` signature.

**Why.** `ReadableSpan.attributes` is immutable, so translation cannot mutate in place. Two options:

- A duck-typed proxy with `__getattr__` delegation. Simpler, but breaks third-party exporters that `isinstance`-check.
- Construct a real `ReadableSpan`. Guaranteed compatible with anything that accepts a span.

We chose the latter. Signature filtering handles OTel SDK version drift — and specifically avoids passing the deprecated `instrumentation_info`, which would emit a `DeprecationWarning` on every span.

**Cost.** One object allocation per span per destination. Off the application's call path (D3).

---

## 7. Configuration

### D15. Four layers, merged field by field

**Decision.** defaults → config file → environment → code kwargs. Each layer overrides the previous **per field**, not wholesale.

**Why.** Wholesale replacement is the common bug: setting one environment variable silently discards everything the config file said. Field-level merge means `OBSERVIX_LANGFUSE_SAMPLE_RATIO=0.25` adjusts one thing and leaves the rest.

**Detail.** Vendor-native environment variables (`LANGFUSE_PUBLIC_KEY`, `PHOENIX_COLLECTOR_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `DD_API_KEY`) are honoured directly. Users already have these set; making them re-declare under an `OBSERVIX_` prefix would be gratuitous.

---

### D16. Dataclasses, not pydantic

**Decision.** Configuration uses stdlib dataclasses with hand-written validation.

**Why.** "Minimal overhead" is a stated priority, and pydantic is a heavy dependency to force on every user of an observability library that may be installed into constrained environments. Our config surface is small enough that hand-rolled validation is a few dozen lines.

**Cost.** We write our own coercion and error messages. Accepted — it also lets us write *actionable* errors ("Install with: `pip install 'observix[otlp]'`") rather than generic schema violations.

---

### D17. Duplicate destination names are a hard error

**Decision.** Two exporters resolving to the same key raises `ConfigurationError`.

**Why.** `exporters=["langfuse", "langfuse"]` is either a copy-paste mistake or an attempt to configure the same provider twice. Silently deduplicating hides the first; silently double-exporting hides the second. The error names the fix: give each a distinct `name`.

---

## 8. Developer experience

### D18. `@observe` works bare and parameterised, across all four function shapes

**Decision.** `@observe` and `@observe(kind=...)` both work, on sync functions, coroutines, generators and async generators.

**Why.** The README promise is `@observe` with no parentheses. Supporting only the parameterised form would break the headline example; supporting only the bare form would make every non-default use awkward.

**Generator detail.** For generators the span covers the **whole iteration**, not just the call that creates the generator object. A span that ends before the work happens measures nothing useful.

---

### D19. Never return `None` from `get_current_span()`

**Decision.** Return a `NoOpSpan` when nothing is active or observix is disabled.

**Why.** Otherwise every call site needs `if span is not None:`. The no-op instance is module-level and reused, so the disabled path allocates nothing.

---

### D20. Test helpers capture spans *after* redaction and translation

**Decision.** `observix.testing.collect_spans(dialect=...)` runs the full pipeline into an in-memory exporter.

**Why.** Asserting on canonical attributes would test our own bookkeeping. Asserting on translated output tests what a backend actually receives — which is the only way to catch a dialect regression before users do. `multi_collector` extends this to assert one run against every backend's rendering at once.

---

## 9. Decisions made during implementation

### D21. `redaction.py` lives at package top level, not inside `pipeline/`

**Decision.** Moved `pipeline/redaction.py` → `redaction.py`.

**Why.** Discovered during the first successful import: `config` imports `RedactionPolicy` → triggers `pipeline/__init__` → imports `builder` → imports `config`. Circular.

The fix is architectural rather than a workaround. `RedactionPolicy` is a **policy value type** that configuration owns; it has no dependency on pipeline machinery. It was in the wrong package. `pipeline/__init__` still re-exports it for convenience.

**Rejected.** Deferred imports inside functions. Would have worked and hidden a genuine layering mistake.

### D22. MIME type detection must account for JSON-in-string

**Decision.** `_mime_for()` inspects whether a string starts with `{` or `[` rather than testing `isinstance(value, str)`.

**Why.** Caught by the first end-to-end run. Canonical attributes store structured data as JSON **strings** (OTel attributes cannot hold nested objects). The naive `isinstance` check therefore labelled every message list `text/plain`, which costs the reader Phoenix's structured message viewer — a silent quality regression that renders fine and is simply worse.

A good illustration of why D20 exists: this was invisible to any test asserting on canonical attributes.

### D23. Cache reads are not billed twice

**Decision.** When a price book entry supplies `cache_read`, `compute_cost` subtracts cached tokens from the billable input count before pricing them at the discounted rate.

**Why.** Providers report cache reads *inside* `input_tokens`. Pricing both at full rate overstates cost, and cost attribution that is quietly wrong is worse than absent.

### D24. The built-in price book is explicitly not a billing source of truth

**Decision.** Ship prices so cost works out of the box; document them as a snapshot; support override via `register_price()` and `OBSERVIX_PRICES_FILE`.

**Why.** Providers change prices without notice and we are not in the pricing business. Shipping nothing makes the feature useless by default; shipping prices while claiming authority would be misleading.

**Detail.** Model matching is exact-then-longest-prefix, after stripping vendor routing prefixes and date/version suffixes — so `anthropic/claude-sonnet-4-20250514` and `us.anthropic.claude-sonnet-4-v1` both resolve via the `claude-sonnet-4` entry. Longest-prefix specifically ensures `gpt-4o-mini` does not resolve to the pricier `gpt-4o`.

---

### D26. Python 3.10 is the floor, not 3.9

**Decision.** Raised `requires-python` from `>=3.9` to `>=3.10` partway through the build.

**Why.** Three reasons converged:

1. Python 3.9 reached end of life in October 2025.
2. The installed mypy **refuses to type-check** `python_version = "3.9"` at all. Shipping a "strict typing" claim we could not actually verify would be dishonest.
3. Nearly 400 of the initial 519 lint findings were "use PEP 585/604 syntax" — `Optional[X]` → `X | None`, `Dict` → `dict`. With `from __future__ import annotations` these are valid even on 3.9, but committing to 3.10 let the whole codebase modernise in one automated pass rather than carrying `typing.Optional` imports forever.

**Cost.** Anyone still on 3.9 cannot install observix. Given 3.9 is EOL and this is a new library with no existing users, that cost is zero today and only grows if we delay.

**Verification.** The 537-fix automated rewrite was validated by the existing 251 tests, all of which continued to pass.

### D27. Adoption normalises *before* redaction

**Decision.** `normalize_foreign_attributes()` runs at the start of `DialectSpanExporter._translate()`, ahead of the redaction policy.

**Why.** Adopted spans carry prompts too. If normalisation ran after redaction, an OpenLLMetry span's `gen_ai.prompt` would arrive at a `redact="none"` destination untouched, because the policy only recognises canonical content keys. Ordering it first means one policy governs native and adopted content identically.

**Related.** Adoption is off by default (it costs an attribute scan per span per destination) and is conservative by construction: a span already carrying any `observix.*` attribute is skipped entirely, so we never re-derive our own output, and an existing canonical value is never overwritten.

---

## 10. Environment decisions (this build)

### D25. uv with a managed CPython, rather than system Python

**Decision.** Installed `uv` via winget and let it manage a CPython 3.12.13 build.

**Why.** The machine had no working interpreter — only the Microsoft Store alias stub. uv manages its own Python builds, gives fast venvs, and doubles as the build/test runner, without touching system PATH.

**Wrinkle.** `uv venv --python 3.12` failed with "Missing expected target directory for Python minor version link" — uv could not create the minor-version symlink (Windows symlinks need Developer Mode or admin). The interpreter itself downloaded fine, so pointing `--python` at the concrete `python.exe` path worked. Worth knowing for anyone reproducing the setup on Windows.

---

## Principles these decisions serve

1. **Never reinvent OpenTelemetry** — D2, D12 (the one justified exception), D14
2. **Fail open** — D6, D7, D8, D13
3. **Canonical in, native out** — D3, D4, D5
4. **Policy belongs to the destination** — D11, D12
5. **Extensible without forking** — D13
6. **Zero-config is a no-op** — D9, D10
