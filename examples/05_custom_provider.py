"""Extending observix: a custom backend and a custom dialect.

Run:  python examples/05_custom_provider.py

Neither of these touches observix's source. In a real package you would declare
them as entry points instead of calling register_*, and they would then be
available by name in configuration:

    [project.entry-points."observix.providers"]
    mybackend = "my_pkg:MyBackendProvider"

    [project.entry-points."observix.dialects"]
    mydialect = "my_pkg:MyDialect"
"""

from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from observix import configure, flush, get_current_span, observe, shutdown
from observix.dialects import CanonicalView, Dialect, TranslationResult, register_dialect
from observix.providers import Provider, ProviderContext, register_provider

# --- A custom dialect: translate canonical telemetry into our own vocabulary --


class AcmeDialect(Dialect):
    """Emit the attribute names Acme's internal trace store expects."""

    name = "acme"

    def __init__(self, *, capture_content: bool = True) -> None:
        self.capture_content = capture_content

    def translate(self, view: CanonicalView) -> TranslationResult:
        result = TranslationResult()

        result.set("acme.op", view.kind.value)
        result.set("acme.model", view.model)
        result.set("acme.vendor", view.provider)

        usage = view.usage
        result.set("acme.tokens.in", usage.input_tokens)
        result.set("acme.tokens.out", usage.output_tokens)
        result.set("acme.usd", view.cost.resolved_total())

        if self.capture_content:
            result.set("acme.prompt", view.input_text())
            result.set("acme.answer", view.output_text())

        # Acme names spans "<vendor>:<op>".
        if view.provider:
            result.name = f"{view.provider}:{view.kind.value}"
        return result


# --- A custom provider: where those spans go ---------------------------------


class AcmeExporter(SpanExporter):
    """Stand-in for a real HTTP client. Prints instead of sending."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            acme = {k: v for k, v in (span.attributes or {}).items() if k.startswith("acme.")}
            print(f"  [acme] POST /traces  name={span.name!r}  {acme}")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None: ...

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


class AcmeProvider(Provider):
    """Acme's trace store."""

    name = "acme"
    default_dialect = "acme"  # the dialect above, by name
    endpoint_env = "ACME_ENDPOINT"

    def create_exporter(self, config, context: ProviderContext) -> SpanExporter:
        # A real provider would build an OTLP exporter here, e.g. with
        # make_otlp_http_exporter(endpoint=self.require_endpoint(config), ...)
        return AcmeExporter()


register_dialect("acme", AcmeDialect)
register_provider("acme", AcmeProvider)


# --- Use it exactly like a built-in -------------------------------------------

configure(service_name="custom-demo", exporters=["acme"], set_global_tracer_provider=False)


@observe(kind="chat")
def call() -> str:
    get_current_span().record_llm_call(
        provider="openai",
        request_model="gpt-4o",
        input_messages=[{"role": "user", "content": "hello"}],
        output_messages=[{"role": "assistant", "content": "hi"}],
        input_tokens=5,
        output_tokens=2,
    )
    return "hi"


if __name__ == "__main__":
    print("Exporting through a third-party provider + dialect:")
    call()
    flush()
    shutdown()
