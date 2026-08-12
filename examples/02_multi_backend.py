"""Multi-backend fan-out: one recording, four native renderings.

Run:  python examples/02_multi_backend.py

Uses in-memory destinations so it runs with no backends installed, and prints
what each one would have received. Swap `provider="memory"` for `"phoenix"`,
`"langfuse"`, `"mlflow"` and `"datadog"` to send the same spans for real ---
the instrumented function does not change.
"""

from observix import ExporterConfig, configure, flush, get_current_span, observe, shutdown
from observix.providers.memory import InMemorySpanExporter

DESTINATIONS = {
    "phoenix (OpenInference)": "openinference",
    "langfuse (langfuse.*)": "langfuse",
    "mlflow (mlflow.*)": "mlflow",
    "datadog (gen_ai.*)": "otel_genai",
}

collectors = {name: InMemorySpanExporter() for name in DESTINATIONS}

configure(
    service_name="multi-backend-demo",
    set_global_tracer_provider=False,
    exporters=[
        ExporterConfig(
            provider="memory",
            name=name,
            dialect=dialect,
            options={"exporter": collectors[name]},
        )
        for name, dialect in DESTINATIONS.items()
    ],
)


@observe(kind="chat")
def summarise(text: str) -> str:
    summary = "A short summary."
    get_current_span().record_llm_call(
        provider="openai",
        request_model="gpt-4o",
        input_messages=[{"role": "user", "content": f"Summarise: {text}"}],
        output_messages=[{"role": "assistant", "content": summary}],
        input_tokens=850,
        output_tokens=45,
        temperature=0.3,
    ).set_session(user_id="u_42", session_id="s_7")
    return summary


if __name__ == "__main__":
    summarise("a long document about observability")
    flush()

    for name, exporter in collectors.items():
        span = exporter.get_finished_spans()[0]
        print(f"\n{'=' * 68}\n{name}  ->  span name: {span.name!r}\n{'=' * 68}")
        for key in sorted(span.attributes):
            value = str(span.attributes[key])
            print(f"  {key} = {value if len(value) < 55 else value[:52] + '...'}")

    print(
        "\nOne @observe. Four vocabularies. Each backend renders natively,\n"
        "and none of them sees another backend's namespace."
    )
    shutdown()
