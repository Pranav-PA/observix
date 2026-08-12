# Configuration reference

Configuration is the *only* thing that changes when you switch backends.

## Layering

Four layers, merged **field by field** — setting one environment variable does not discard everything the file said.

```
defaults  →  config file  →  environment  →  configure() kwargs
   (lowest)                                        (highest)
```

## Global options

| Option | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `True` | Master switch. `False` makes `@observe` a no-op. |
| `service_name` | str | `unknown_service` | Recorded on every span. |
| `service_version` | str | — | Recorded on every span. |
| `environment` | str | — | `production`, `staging`, … |
| `exporters` | list | `[]` | Destinations. See below. |
| `sample_ratio` | float | `1.0` | Global head sampling, `0.0`–`1.0`. |
| `capture_content` | bool | `True` | Master switch for prompts/completions. |
| `redact` | str/dict | — | Default policy for destinations without their own. |
| `adopt_foreign` | bool | `False` | Adopt OpenLLMetry/OpenInference/MLflow spans. See [adopting](adopting.md). |
| `resource_attributes` | dict | `{}` | Extra OTel resource attributes. |
| `batch` | dict | `{}` | Default `BatchSpanProcessor` options. |
| `set_global_tracer_provider` | bool | `True` | Register with OTel globally. |

## Per-destination options

| Option | Type | Default | Meaning |
|---|---|---|---|
| `provider` | str | *required* | Registered provider name. |
| `name` | str | = provider | Identifier. Set it to use one provider twice. |
| `dialect` | str | provider's default | Override the vocabulary. |
| `endpoint` | str | provider's default | Override the URL. |
| `headers` | dict | `{}` | Merged over provider-built headers. |
| `protocol` | str | `http/protobuf` | Or `http/json`, `grpc` where supported. |
| `timeout` | float | — | Export timeout, seconds. |
| `redact` | str/dict | inherits global | This destination's privacy policy. |
| `sample_ratio` | float | `1.0` | Fraction of traces this destination receives. |
| `batch` | dict | `{}` | `BatchSpanProcessor` overrides. |
| `options` | dict | `{}` | Provider-specific settings. |
| `enabled` | bool | `True` | Configure without activating. |

`batch` accepts `max_queue_size`, `schedule_delay_millis`, `max_export_batch_size`, `export_timeout_millis`.

## In code

```python
from observix import configure

configure(
    service_name="my-app",
    environment="production",
    exporters=[
        "phoenix",  # by name
        {"provider": "langfuse", "sample_ratio": 0.25},  # by mapping
        {"provider": "datadog", "redact": "none"},
        {
            "provider": "otlp",
            "name": "tempo",  # same provider twice
            "endpoint": "http://tempo:4318",
        },
    ],
)
```

## In a file

`observix.toml` in your project root, or `[tool.observix]` in `pyproject.toml`. Discovery walks up from the working directory.

```toml
[observix]
service_name = "my-app"
environment  = "production"
sample_ratio = 1.0

[observix.exporters.phoenix]
# table key becomes both name and provider

[observix.exporters.langfuse]
sample_ratio = 0.25
redact = "hashed"

[observix.exporters.tempo]
provider = "otlp"          # explicit provider when name differs
endpoint = "http://tempo:4318"
```

## In the environment

**Global:**

```bash
OBSERVIX_ENABLED=true
OBSERVIX_SERVICE_NAME=my-app
OBSERVIX_SERVICE_VERSION=1.4.2
OBSERVIX_ENVIRONMENT=production
OBSERVIX_EXPORTERS=phoenix,langfuse
OBSERVIX_SAMPLE_RATIO=1.0
OBSERVIX_CAPTURE_CONTENT=true
OBSERVIX_REDACT=all
OBSERVIX_ADOPT_FOREIGN=false
OBSERVIX_STRICT=0          # 1 re-raises internal errors; for debugging
```

**Per destination** — `OBSERVIX_<NAME>_<OPTION>`, where `<NAME>` is the destination name uppercased:

```bash
OBSERVIX_LANGFUSE_ENDPOINT=https://langfuse.internal
OBSERVIX_LANGFUSE_SAMPLE_RATIO=0.25
OBSERVIX_LANGFUSE_REDACT=hashed
OBSERVIX_LANGFUSE_HEADERS=x-team=platform,x-tier=gold
OBSERVIX_LANGFUSE_PROTOCOL=http/protobuf
OBSERVIX_LANGFUSE_TIMEOUT=10
OBSERVIX_LANGFUSE_ENABLED=true
```

**Vendor-native variables are honoured directly** — you already have these set, so observix does not make you re-declare them: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_API_KEY`, `ARIZE_SPACE_ID`, `ARIZE_API_KEY`, `DD_API_KEY`, `DD_SITE`, `MLFLOW_TRACKING_URI`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_SERVICE_NAME`.

## Redaction

Privacy is a property of the **destination**. Full prompts to your self-hosted Langfuse, hashed to staging, none to a third party — from one recording.

```python
configure(
    exporters=[
        {"provider": "langfuse"},  # full content
        {"provider": "phoenix", "redact": "hashed"},  # unreadable, still joinable
        {"provider": "datadog", "redact": "none"},  # metrics only
    ]
)
```

**Modes**

| Mode | Effect |
|---|---|
| `all` | Send everything. Default. |
| `none` | Drop prompts, completions, documents, tool payloads. |
| `hashed` | Replace with `sha256:<16 hex>` — unreadable but joinable. |
| `truncated` | Keep a bounded prefix (default 512 chars). |

**Full policy form:**

```python
{
    "provider": "phoenix",
    "redact": {
        "mode": "truncated",
        "max_length": 200,
        "redact_keys": ["password", "api[_-]?key"],  # regexes on attribute *names*
        "detect_pii": True,
        "pii_types": ["email", "credit_card", "ssn", "phone", "ipv4"],
        "hash_salt": "per-destination-salt",
    },
}
```

Token counts, model names, timings and cost are **never** redacted — dashboards keep working at every level.

> The PII detectors are regexes for common accidental leaks. They are **not** a compliance control; do not rely on them as one.

When *no* destination retains content, observix skips prompt serialisation entirely.

## Sampling

Two independent layers:

```python
configure(
    sample_ratio=0.5,  # global: half of all traces
    exporters=[
        {"provider": "langfuse"},  # gets all of that half
        {"provider": "datadog", "sample_ratio": 0.1},  # gets 10% of that half
    ],
)
```

Per-destination ratios use the same trace-id hash as OTel's `TraceIdRatioBased`, so whole traces are kept and destinations at the same ratio agree on which.

**Custom predicate** — drop noisy spans from one destination:

```python
{"provider": "datadog", "options": {"predicate": lambda span: span.name != "health_check"}}
```

## Cost

Built-in USD prices cover the common models. Override:

```python
from observix.cost import register_price, ModelPrice

register_price("my-finetune", ModelPrice(input=2.0, output=8.0))
```

Or from a file:

```bash
export OBSERVIX_PRICES_FILE=/etc/observix/prices.json
```

```json
{"my-model": {"input": 1.0, "output": 3.0, "cache_read": 0.1}}
```

Prices are USD per **million** tokens. Matching is exact, then longest prefix, after stripping vendor routing prefixes and date suffixes — so `anthropic/claude-sonnet-4-20250514` resolves via `claude-sonnet-4`.

> The built-in price book is a snapshot, not a billing source of truth. Providers change prices without notice.

## Lifecycle

```python
from observix import configure, flush, shutdown, is_configured, get_config, get_pipelines

configure(...)  # safe to call again; tears the previous setup down first
flush(timeout_millis=30_000)
shutdown()  # idempotent
is_configured()  # bool
get_config()  # effective ObservixConfig
get_pipelines()  # active destinations, for diagnostics
```

## Diagnostics

```python
import logging

logging.getLogger("observix").setLevel(logging.DEBUG)
```

`OBSERVIX_STRICT=1` re-raises internal errors instead of suppressing them. Use it when debugging a custom provider or dialect — never in production.
