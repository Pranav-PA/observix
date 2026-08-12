# Benchmarks

```bash
python benchmarks/bench_overhead.py
```

Measures observix's own work against in-memory destinations, so no network
latency is included. Reports nanoseconds per call, median of 5 repeats,
GC disabled.

## Results

Measured on Windows 10, Python 3.12.13, an ordinary developer laptop. **Absolute
values are machine-specific and noisy — the baseline alone varied 120–230 ns
between runs. The ratios are what matter.**

| Scenario | ns/call | vs. baseline |
|---|---:|---:|
| Undecorated function call (baseline) | ~200 | — |
| `@observe`, observix never configured | ~585 | +~400 |
| `@observe`, `enabled=False` | ~770 | +~570 |
| **Raw OpenTelemetry** (`start_as_current_span` + 2 attrs) | **~56,000** | +55,950 |
| `@observe`, enabled, no capture | ~85,400 | +85,250 |
| `@observe`, enabled, capturing args + result | ~118,100 | +117,950 |

## What these say about the design

**D9 — disabled observix is cheap. Broadly confirmed, with a correction.**
An unconfigured `@observe` costs roughly **0.4 µs**. That is sub-microsecond and
negligible against almost any real function body. But the original claim of
"one attribute load" was too generous: the real cost is a `functools.wraps`
wrapper invocation *plus* the runtime check. Sub-microsecond, not free.

**Enabled cost is dominated by OpenTelemetry, not by observix.**
Raw OTel span creation is ~56 µs on this machine. observix adds ~29 µs on top —
its facade, canonical attribute writes, and context handling. Worth knowing:
about two-thirds of an instrumented span's cost is the SDK underneath, so the
headline number is mostly a statement about OTel and this machine, not about
this library.

**D10 — skipping content serialisation is real.**
A full LLM span with messages, usage and cost costs ~314 µs when content is
retained and ~213 µs when every destination redacts it — **~32 % of the work
avoided** by the check that runs before the JSON encoder. That is the whole
point of computing `record_content` once at `configure()` time.

**Fan-out is linear and cheap on the application thread.**
Each extra destination adds ~22 µs to the calling thread — a `FilteringSpanProcessor`
check and a queue append. Translation and network I/O happen on each
destination's own worker, so the marginal cost of a fourth backend is a queue
append, not a fourth export.

| Destinations | ns/call |
|---:|---:|
| 1 | ~120,000 |
| 2 | ~146,000 |
| 4 | ~188,000 |

**Dialect translation is off the call path entirely.**

| Dialect | ns/span |
|---|---:|
| `mlflow` | ~33,000 |
| `langfuse` | ~50,000 |
| `otel_genai` | ~55,000 |
| `openinference` | ~78,000 |

`openinference` is the most expensive because it flattens messages and documents
into indexed keys — that cost buys Phoenix's per-message trace view, and it is
paid on the exporter's thread, never the caller's.

## Optimisations these measurements prompted

Both came directly from reading the first run, and both are in the code now:

1. **`_finish` reuses the span facade from `_begin`** instead of allocating a
   second `ObservixSpan` and re-reading the runtime.
2. **Argument capture zips pre-resolved parameter names** rather than calling
   `inspect.Signature.bind_partial` on every call. Functions with `*args` still
   take the full-binding path, since positional zipping cannot name a variadic
   tail.

Capture cost fell from ~39 µs to ~33 µs (about 16 %). Less than hoped, which is
itself informative: `bind_partial` was not the dominant term — JSON
serialisation and `set_attribute` are.

## Where the remaining cost is

If someone wants to push further, the measurements point at:

- `ObservixSpan._set` calls `is_recording` per attribute; a span writing ten
  attributes makes ten such calls.
- Every non-scalar value goes through `to_json`.
- OTel's own `set_attribute` does bounds and type checking per call.

None of this is on the exporter thread, so it is the part that genuinely
competes with application work.

## Caveats

- Single machine, single OS, no repetitions across hardware.
- In-memory destinations; a real OTLP exporter adds serialisation and network
  on its own thread.
- Benchmarks are not run in CI, so these numbers are not regression-guarded.
