"""Measure what observix actually costs.

Two claims in the design deserve numbers rather than adjectives:

* **D9** --- disabled observix costs one attribute load.
* **D10** --- content serialisation is skipped when no destination retains it.

Run:  python benchmarks/bench_overhead.py

Uses in-memory destinations so the measurement is observix's own work, not
network latency. Reports nanoseconds per call, so the numbers are comparable
across machines even though the absolute values are not.
"""

from __future__ import annotations

import gc
import statistics
import time
from collections.abc import Callable

from observix import ExporterConfig, configure, flush, get_current_span, observe, shutdown
from observix.dialects import (
    CanonicalView,
    LangfuseDialect,
    MLflowDialect,
    OpenInferenceDialect,
    OTelGenAIDialect,
)
from observix.providers.memory import InMemorySpanExporter
from observix.semconv import canonical as C

ITERATIONS = 20_000
REPEATS = 5


def measure(fn: Callable[[], object], *, iterations: int = ITERATIONS) -> float:
    """Median nanoseconds per call across repeats, GC disabled."""
    timings: list[float] = []
    gc.collect()
    gc.disable()
    try:
        for _ in range(REPEATS):
            start = time.perf_counter_ns()
            for _ in range(iterations):
                fn()
            timings.append((time.perf_counter_ns() - start) / iterations)
    finally:
        gc.enable()
    return statistics.median(timings)


def row(label: str, ns: float, baseline: float | None = None) -> str:
    overhead = "" if baseline is None else f"{ns - baseline:>12,.0f}"
    return f"  {label:<44} {ns:>10,.0f} ns {overhead}"


# --- The workload ------------------------------------------------------------


def bare(a: int, b: int) -> int:
    return a + b


@observe
def decorated(a: int, b: int) -> int:
    return a + b


@observe(capture_input=False, capture_output=False)
def decorated_no_capture(a: int, b: int) -> int:
    return a + b


@observe(kind="chat")
def llm_call(prompt: str) -> str:
    get_current_span().record_llm_call(
        provider="anthropic",
        request_model="claude-opus-4",
        input_messages=[{"role": "user", "content": prompt}],
        output_messages=[{"role": "assistant", "content": "response"}],
        input_tokens=1200,
        output_tokens=340,
        temperature=0.7,
    )
    return "response"


def main() -> None:
    print("=" * 76)
    print("observix overhead".center(76))
    print("=" * 76)

    baseline = measure(lambda: bare(1, 2))
    print("\nBaseline")
    print(row("undecorated function call", baseline))

    # --- D9: disabled ---------------------------------------------------------
    shutdown()
    disabled = measure(lambda: decorated(1, 2))
    print("\nD9 - observix never configured (the default for a library user)")
    print(row("@observe, disabled", disabled, baseline))

    configure(service_name="bench", enabled=False, set_global_tracer_provider=False)
    explicit_off = measure(lambda: decorated(1, 2))
    print(row("@observe, enabled=False", explicit_off, baseline))
    shutdown()

    # --- Raw OpenTelemetry, for attribution -----------------------------------
    # Without this, an "enabled" number says nothing about whether the cost is
    # observix's or the SDK's underneath it.
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    raw_provider = TracerProvider()
    raw_provider.add_span_processor(BatchSpanProcessor(InMemorySpanExporter()))
    raw_tracer = raw_provider.get_tracer("bench")

    def raw_otel() -> int:
        with raw_tracer.start_as_current_span("bare") as span:
            span.set_attribute("a", 1)
            span.set_attribute("b", 2)
            return bare(1, 2)

    otel_only = measure(raw_otel, iterations=5_000)
    raw_provider.shutdown()

    print("\nRaw OpenTelemetry, no observix (attribution baseline)")
    print(row("start_as_current_span + 2 attributes", otel_only, baseline))

    # --- Enabled --------------------------------------------------------------
    memory = InMemorySpanExporter()
    configure(
        service_name="bench",
        set_global_tracer_provider=False,
        exporters=[ExporterConfig(provider="memory", options={"exporter": memory})],
    )
    enabled_capture = measure(lambda: decorated(1, 2), iterations=5_000)
    enabled_no_capture = measure(lambda: decorated_no_capture(1, 2), iterations=5_000)
    memory.clear()

    print("\nEnabled, one in-memory destination")
    print(row("@observe, capturing args + result", enabled_capture, baseline))
    print(row("@observe, no capture", enabled_no_capture, baseline))
    print(
        f"  {'-> observix cost above raw OTel (no capture)':<44} "
        f"{enabled_no_capture - otel_only:>10,.0f} ns"
    )
    print(
        f"  {'-> cost of capturing args + result':<44} "
        f"{enabled_capture - enabled_no_capture:>10,.0f} ns"
    )
    shutdown()

    # --- D10: content skipped when nothing retains it -------------------------
    memory = InMemorySpanExporter()
    configure(
        service_name="bench",
        set_global_tracer_provider=False,
        exporters=[ExporterConfig(provider="memory", redact="none", options={"exporter": memory})],
    )
    llm_redacted = measure(lambda: llm_call("a prompt " * 40), iterations=3_000)
    memory.clear()
    shutdown()

    memory = InMemorySpanExporter()
    configure(
        service_name="bench",
        set_global_tracer_provider=False,
        exporters=[ExporterConfig(provider="memory", options={"exporter": memory})],
    )
    llm_full = measure(lambda: llm_call("a prompt " * 40), iterations=3_000)
    memory.clear()
    shutdown()

    print("\nD10 - a full LLM span (messages, usage, cost)")
    print(row("recording content", llm_full, baseline))
    print(row("every destination redacts (serialisation skipped)", llm_redacted, baseline))
    saved = (1 - llm_redacted / llm_full) * 100 if llm_full else 0
    print(f"  {'-> content work avoided':<44} {saved:>10.1f} %")

    # --- Fan-out scaling ------------------------------------------------------
    print("\nFan-out - cost of adding destinations (application thread only)")
    for count in (1, 2, 4):
        exporters = [
            ExporterConfig(
                provider="memory", name=f"d{i}", options={"exporter": InMemorySpanExporter()}
            )
            for i in range(count)
        ]
        configure(service_name="bench", set_global_tracer_provider=False, exporters=exporters)
        per_call = measure(lambda: decorated(1, 2), iterations=5_000)
        flush()
        shutdown()
        print(row(f"{count} destination(s)", per_call, baseline))

    # --- Dialect translation (exporter thread, off the call path) -------------
    print("\nDialect translation - per span, on the exporter thread")
    view = CanonicalView(
        {
            C.KIND: "chat",
            C.LLM_PROVIDER: "anthropic",
            C.LLM_REQUEST_MODEL: "claude-opus-4",
            C.LLM_REQUEST_TEMPERATURE: 0.7,
            C.INPUT_MESSAGES: (
                '[{"role":"user","parts":[{"type":"text","content":"Hello there"}]}]'
            ),
            C.OUTPUT_MESSAGES: ('[{"role":"assistant","parts":[{"type":"text","content":"Hi"}]}]'),
            C.USAGE_INPUT_TOKENS: 1200,
            C.USAGE_OUTPUT_TOKENS: 340,
            C.COST_TOTAL_USD: 0.0435,
            C.SESSION_ID: "s_1",
        },
        name="call_model",
    )
    for dialect in (
        OTelGenAIDialect(),
        OpenInferenceDialect(),
        LangfuseDialect(),
        MLflowDialect(),
    ):
        # A fresh view per call, so lazy message parsing is measured honestly.
        ns = measure(
            lambda d=dialect: d(CanonicalView(view.attributes, name="call_model")),
            iterations=20_000,
        )
        print(row(dialect.name, ns))

    print("\n" + "=" * 76)
    print("Absolute values are machine-specific; the ratios are the point.")
    print("=" * 76)


if __name__ == "__main__":
    main()
