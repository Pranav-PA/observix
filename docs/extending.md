# Extending observix

Adding a backend never requires changing observix. Two registries, both entry-point backed:

- **`observix.providers`** — *where* telemetry goes: endpoint, auth, protocol
- **`observix.dialects`** — *what shape* it arrives in

## Add a provider

Most backends speak OTLP, so subclass `OTLPProviderBase` and supply endpoint and auth:

```python
# my_pkg/provider.py
from observix.providers import OTLPProviderBase


class MyBackendProvider(OTLPProviderBase):
    name = "mybackend"
    default_dialect = "otel_genai"
    endpoint_env = "MYBACKEND_ENDPOINT"
    traces_path = "/ingest/v1/traces"

    def resolve_endpoint(self, config):
        endpoint = super().resolve_endpoint(config)
        if endpoint:
            return endpoint
        region = config.options.get("region", "us")
        return f"https://{region}.mybackend.io{self.traces_path}"

    def build_headers(self, config):
        import os

        api_key = config.options.get("api_key") or os.environ.get("MYBACKEND_API_KEY")
        if not api_key:
            from observix.errors import ConfigurationError

            raise ConfigurationError(
                "mybackend requires an API key. Set MYBACKEND_API_KEY, or pass "
                "api_key in the exporter's options."
            )
        headers = {"authorization": f"Bearer {api_key}"}
        headers.update(config.headers)  # user-supplied headers win
        return headers
```

For a backend that doesn't speak OTLP, subclass `Provider` and return any `SpanExporter`:

```python
from observix.providers import Provider, ProviderContext


class MyProvider(Provider):
    name = "mybackend"
    default_dialect = "otel_genai"

    def create_exporter(self, config, context: ProviderContext):
        return MyCustomSpanExporter(url=config.endpoint)
```

`ProviderContext` carries `service_name`, `service_version`, `environment` and `resource_attributes`.

### Raise `ConfigurationError` for missing setup

Configuration errors are the one class observix does not suppress. Make the message say what to do — the user is reading it because something did not work.

## Add a dialect

```python
# my_pkg/dialect.py
from observix.dialects import CanonicalView, Dialect, TranslationResult


class MyDialect(Dialect):
    name = "mybackend"

    def __init__(self, *, capture_content: bool = True) -> None:
        self.capture_content = capture_content

    def translate(self, view: CanonicalView) -> TranslationResult:
        result = TranslationResult()
        result.set("mb.kind", view.kind.value)
        result.set("mb.model", view.model)
        result.set("mb.vendor", view.provider)

        usage = view.usage
        result.set("mb.tokens.input", usage.input_tokens)
        result.set("mb.tokens.output", usage.output_tokens)
        result.set("mb.cost_usd", view.cost.resolved_total())

        if self.capture_content:
            result.set("mb.input", view.input_text())
            result.set("mb.output", view.output_text())

        for key, value in view.metadata.items():
            result.set(f"mb.meta.{key}", value)

        return result
```

### The `CanonicalView` API

Lazy and typed — decoding only happens for what you read.

| Member | Type | Notes |
|---|---|---|
| `kind` | `SpanKind` | Always present; defaults to `TASK` |
| `provider` / `model` | `str \| None` | `model` prefers response over request |
| `request_model` / `response_model` | `str \| None` | |
| `input_messages` / `output_messages` | `list[Message]` | Parsed lazily |
| `input_text()` / `output_text()` | `str \| None` | Flattened, for scalar backends |
| `resolved_input()` / `resolved_output()` | `Any` | Messages if present, else raw I/O |
| `system_instructions` | `str \| None` | |
| `usage` | `TokenUsage` | `.resolved_total()` derives when absent |
| `cost` | `Cost` | |
| `session_id` / `user_id` / `conversation_id` | `str \| None` | |
| `tags` | `list[str] \| None` | |
| `metadata` | `dict[str, Any]` | Prefix already stripped |
| `finish_reasons` | `Sequence[str] \| None` | |
| `span_name` | `str` | |
| `get(key, default)` | `Any` | Any canonical key |
| `passthrough_attributes()` | `dict` | Non-`observix.*` attributes |

### Rules

1. **`TranslationResult.set()` ignores `None`.** Absent data stays absent — OTel rejects `None`.
2. **Accept `capture_content: bool = True`** so the global switch reaches you.
3. **Stay pure.** No I/O, no globals, no mutation of the view.
4. **Set `result.name`** only if the backend expects a naming convention.
5. **Don't re-add foreign attributes** — the base class merges them for you.

## Register

**In-process**, for application code or tests:

```python
from observix import register_provider, register_dialect

register_provider("mybackend", MyBackendProvider)
register_dialect("mybackend", MyDialect)
```

**As a package**, so anyone can use it by name:

```toml
[project.entry-points."observix.providers"]
mybackend = "my_pkg.provider:MyBackendProvider"

[project.entry-points."observix.dialects"]
mybackend = "my_pkg.dialect:MyDialect"
```

Then, with no change to observix:

```python
configure(exporters=["mybackend"])
```

Explicit registration overrides entry-point discovery, so a user can shadow your provider without forking it. A plugin that fails to import is logged and skipped — one broken package cannot stop the rest of the pipeline from starting.

## Test it

```python
from observix.testing import collect_spans
from observix import ExporterConfig
from observix.providers.memory import InMemorySpanExporter


def test_my_dialect():
    memory = InMemorySpanExporter()
    with collect_spans(
        exporters=[
            ExporterConfig(provider="memory", dialect="mybackend", options={"exporter": memory})
        ]
    ) as spans:
        my_instrumented_function()

    assert spans.one().attributes["mb.kind"] == "chat"
```

Unit-test the dialect directly too — it is a pure function:

```python
from observix.dialects import CanonicalView
from observix.semconv import canonical as C


def test_maps_tokens():
    view = CanonicalView({C.KIND: "chat", C.USAGE_INPUT_TOKENS: 100})
    assert MyDialect()(view).attributes["mb.tokens.input"] == 100
```

## Custom sampling predicates

Drop specific spans from one destination:

```python
configure(
    exporters=[
        {
            "provider": "datadog",
            "options": {
                "predicate": lambda span: not span.name.startswith("health"),
            },
        },
    ]
)
```

Predicates run at `on_end`, so span attributes are populated. A predicate that raises fails open — the span is forwarded.

## A complete worked example

See [`examples/05_custom_provider.py`](../examples/05_custom_provider.py) — a runnable custom provider and dialect in about 100 lines.
