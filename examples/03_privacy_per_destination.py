"""Per-destination privacy: full prompts here, hashed there, none over there.

Run:  python examples/03_privacy_per_destination.py

The same recorded span reaches three destinations at three different privacy
levels. Redaction is a property of the *destination*, so this cannot be
expressed by redacting at record time --- at that point the span is headed for
all three.

Note that metrics survive every policy: token counts, model names and timings
are never redacted, so cost and latency dashboards keep working even where
prompts are dropped entirely.
"""

from observix import ExporterConfig, configure, flush, get_current_span, observe, shutdown
from observix.providers.memory import InMemorySpanExporter

self_hosted = InMemorySpanExporter()  # trusted: full content
staging = InMemorySpanExporter()  # shared: hashed, still joinable
third_party = InMemorySpanExporter()  # external: metrics only

configure(
    service_name="privacy-demo",
    set_global_tracer_provider=False,
    exporters=[
        ExporterConfig(
            provider="memory",
            name="self-hosted",
            dialect="passthrough",
            options={"exporter": self_hosted},
        ),
        ExporterConfig(
            provider="memory",
            name="staging",
            dialect="passthrough",
            redact={"mode": "hashed", "hash_salt": "staging-salt"},
            options={"exporter": staging},
        ),
        ExporterConfig(
            provider="memory",
            name="third-party",
            dialect="passthrough",
            redact="none",
            options={"exporter": third_party},
        ),
    ],
)


@observe(kind="chat")
def handle_support_ticket(message: str) -> str:
    reply = "We have reset your password."
    get_current_span().record_llm_call(
        provider="anthropic",
        request_model="claude-sonnet-4",
        input_messages=[{"role": "user", "content": message}],
        output_messages=[{"role": "assistant", "content": reply}],
        input_tokens=320,
        output_tokens=18,
    )
    return reply


if __name__ == "__main__":
    handle_support_ticket("I'm alice@example.com and I can't log in, card ending 4321")
    flush()

    for label, exporter in (
        ("self-hosted", self_hosted),
        ("staging", staging),
        ("third-party", third_party),
    ):
        attrs = exporter.get_finished_spans()[0].attributes
        content = attrs.get("observix.input.messages", "<dropped entirely>")
        print(f"\n--- {label} ---")
        print(f"  prompt : {str(content)[:80]}")
        print(f"  tokens : {attrs.get('observix.usage.input_tokens')}  (never redacted)")
        print(f"  cost   : {attrs.get('observix.cost.total_usd')}  (never redacted)")

    shutdown()
