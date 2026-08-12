# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is `0.x`, minor releases may contain breaking changes; the
canonical `observix.*` attribute namespace is stable from `1.0` onward.

## [Unreleased]

### Added

- **Streaming support.** `observe_stream` / `observe_astream` wrap a chunk
  iterator, recording time-to-first-token, the accumulated response and the
  chunk count. Finalises correctly when a stream is abandoned part-way.
  `StreamRecorder` is exposed for manual control.
- **Live backend tests** (`tests/live/`, marked `live`, deselected by default)
  that send real spans to a running Phoenix and assert it recognises them.
- **Per-destination resource overrides** via `Provider.resource_overrides()`,
  for backends that route on a resource attribute.
- **Benchmarks** (`benchmarks/`) measuring decorator overhead, the content-skip
  optimisation, fan-out scaling and per-dialect translation cost, against a raw
  OpenTelemetry baseline. Results and caveats in `benchmarks/README.md`.

### Fixed

- **Phoenix project routing.** Projects are selected by the
  `openinference.project.name` *resource* attribute; the previously-sent
  `x-phoenix-project-name` header is ignored by Phoenix, so every span landed in
  `default`. Found by the new live tests.
- Resource merging used `Resource.create()`, whose injected defaults
  (`service.name=unknown_service`) overwrote real values.

### Changed

- `@observe` reuses the span facade from span start to span end instead of
  allocating a second one.
- Argument capture zips pre-resolved parameter names rather than calling
  `inspect.Signature.bind_partial` per call (~16 % faster capture). Functions
  with `*args` still use full binding.

## [0.1.0] — 2026-08-12

First release. The core thesis is in place: instrument once, export natively to
many backends at the same time.

### Added

**Developer API**
- `@observe` decorator, bare or parameterised, for sync functions, coroutines,
  generators and async generators. Generator spans cover the whole iteration.
- `observe_block()` context manager, `start_span()` for manual lifetimes, and
  `get_current_span()` which always returns a usable span.
- `ObservixSpan` facade with typed setters: `record_llm_call`, `set_usage`,
  `set_cost`, `set_tool`, `set_retrieval`, `set_session`, `set_prompt`,
  `set_metadata`, and more.
- Context propagation helpers: `inject_context`, `extract_context`,
  `attach_context`, `current_trace_id`.

**Canonical model**
- `observix.*` attribute namespace, insulating applications from the still-
  Development-status OpenTelemetry GenAI conventions.
- Message normalisation accepting OpenAI-style dicts, Anthropic content blocks,
  plain strings and canonical objects. Images and audio are recorded by
  reference, never embedded.
- `TokenUsage` and `Cost` value objects with derived totals.

**Dialects** — canonical → backend vocabulary, applied at export time
- `otel_genai` (`gen_ai.*`, structured plus legacy flat content)
- `openinference` (Phoenix / Arize, with indexed message flattening)
- `langfuse` (`langfuse.*`, the namespace Langfuse treats as highest precedence)
- `mlflow` (`mlflow.*`)
- `passthrough` (identity, for debugging)

**Providers**
- `console`, `memory`, `otlp`, `phoenix`, `langfuse`, `mlflow`, `arize`, `datadog`
- Endpoint resolution and auth from each vendor's own environment variables.

**Pipeline**
- In-process fan-out to many destinations, each with an independent queue and
  worker thread — no OpenTelemetry Collector required.
- `DialectSpanExporter`: per-destination redaction and translation.
- `FilteringSpanProcessor`: per-destination sampling, trace-id-consistent with
  OTel's `TraceIdRatioBased`.
- A destination that fails to build is skipped rather than taking down the rest.
- A dialect that fails to translate exports the span untranslated rather than
  dropping it.

**Privacy**
- Per-destination `RedactionPolicy` with `all` / `none` / `hashed` / `truncated`
  modes, key-pattern redaction, opt-in PII detection and per-destination salting.
- Metrics, model names and timings are never redacted.
- Content serialisation is skipped entirely when no destination retains content.

**Configuration**
- Four layers merged field by field: defaults → file → environment → code.
- `observix.toml` or `[tool.observix]` in `pyproject.toml`.
- `OBSERVIX_*` variables, global and per destination.
- Vendor-native variables honoured directly.

**Cost**
- Built-in USD price book with exact-then-longest-prefix model matching, vendor
  routing-prefix and date-suffix stripping, and correct cached-token pricing.
- Override via `register_price()` or `OBSERVIX_PRICES_FILE`.

**Interoperability**
- `adopt_foreign=True` maps inbound OpenLLMetry / OpenInference / MLflow /
  Traceloop spans onto the canonical model, so existing instrumentation gains
  fan-out and redaction with no re-instrumentation.

**Extensibility**
- `observix.providers` and `observix.dialects` entry-point groups. Explicit
  registration overrides discovery; a broken plugin is skipped, not fatal.

**Quality**
- `py.typed`, strict mypy, `ParamSpec`-preserving decorators.
- 270+ tests. Five runnable examples, all exercised in CI.
- `observix.testing` with `collect_spans` and `multi_collector`, capturing spans
  after redaction and translation.

### Notes

- Requires Python 3.10+.
- The built-in price book is a snapshot, not a billing source of truth.
- The PII detectors catch common accidental leaks; they are not a compliance
  control.

[Unreleased]: https://github.com/Pranav-PA/observix/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Pranav-PA/observix/releases/tag/v0.1.0
