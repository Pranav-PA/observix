# Providers

A **provider** knows how to reach one backend: default endpoint, protocol, auth headers, and which dialect renders best there. Providers do no transport work of their own — they return a standard OpenTelemetry exporter.

| Provider | Default dialect | Default endpoint | Auth |
|---|---|---|---|
| `console` | `passthrough` | stdout | — |
| `memory` | `passthrough` | in-process | — |
| `otlp` | `otel_genai` | `$OTEL_EXPORTER_OTLP_ENDPOINT` | `$OTEL_EXPORTER_OTLP_HEADERS` |
| `phoenix` | `openinference` | `http://localhost:6006` | optional API key |
| `langfuse` | `langfuse` | region-based cloud host | Basic (public/secret key) |
| `mlflow` | `mlflow` | `http://localhost:5000` | optional bearer token |
| `arize` | `openinference` | `https://otlp.arize.com` | space id + API key |
| `datadog` | `otel_genai` | `http://localhost:4318` (Agent) | API key for intake |

---

## `phoenix` — Arize Phoenix

```python
configure(exporters=["phoenix"])
```

```bash
docker run -p 6006:6006 arizephoenix/phoenix
```

| Option | Env | Notes |
|---|---|---|
| `api_key` | `PHOENIX_API_KEY` | Sent as `authorization: Bearer …` |
| `project_name` | `PHOENIX_PROJECT_NAME` | Sent as `x-phoenix-project-name` |
| — | `PHOENIX_COLLECTOR_ENDPOINT` | Overrides the endpoint |
| — | `PHOENIX_CLIENT_HEADERS` | Extra headers, `k=v,k=v` |

---

## `langfuse` — Langfuse Cloud or self-hosted

```python
configure(exporters=[{"provider": "langfuse", "region": "us"}])
```

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
```

| Option | Env | Notes |
|---|---|---|
| `public_key` | `LANGFUSE_PUBLIC_KEY` | Required |
| `secret_key` | `LANGFUSE_SECRET_KEY` | Required |
| `region` | `LANGFUSE_REGION` | `eu` (default), `us`, `jp`, `hipaa` |
| — | `LANGFUSE_HOST` | Self-hosted; overrides region |
| `ingestion_version` | — | Default `4` |

Self-hosted:

```bash
export LANGFUSE_HOST=https://langfuse.internal
```

> Langfuse does **not** accept OTLP over gRPC. Setting `protocol="grpc"` raises a `ConfigurationError` rather than failing silently at export time.

The path `/api/public/otel/v1/traces` is appended automatically unless your URL already names a route.

---

## `mlflow` — MLflow tracing

```python
configure(exporters=[{"provider": "mlflow", "experiment_id": "42"}])
```

| Option | Env |
|---|---|
| `experiment_id` | `MLFLOW_EXPERIMENT_ID` |
| `token` | `MLFLOW_TRACKING_TOKEN` |
| — | `MLFLOW_TRACKING_URI` |

---

## `arize` — Arize AX

```python
configure(exporters=["arize"])
```

```bash
export ARIZE_SPACE_ID=...
export ARIZE_API_KEY=...
```

| Option | Env |
|---|---|
| `space_id` | `ARIZE_SPACE_ID` |
| `api_key` | `ARIZE_API_KEY` |
| `model_id` | `ARIZE_MODEL_ID` |

---

## `datadog`

Two shapes. **Agent** (default) sends OTLP to a local Datadog Agent:

```python
configure(exporters=["datadog"])
```

**Intake** sends straight to Datadog — set `site`, which then requires an API key:

```python
configure(exporters=[{"provider": "datadog", "site": "datadoghq.com"}])
```

```bash
export DD_API_KEY=...
```

---

## `otlp` — anything else

Grafana Tempo, Jaeger, Honeycomb, SigNoz, New Relic, your own Collector:

```python
configure(
    exporters=[
        {"provider": "otlp", "name": "tempo", "endpoint": "http://tempo:4318"},
        {
            "provider": "otlp",
            "name": "honeycomb",
            "endpoint": "https://api.honeycomb.io",
            "headers": {"x-honeycomb-team": "..."},
        },
    ]
)
```

Respects `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, and `OTEL_EXPORTER_OTLP_TRACES_HEADERS`.

---

## `console` and `memory`

`console` prints spans as JSON — the fastest way to see what a dialect produces:

```python
configure(exporters=[{"provider": "console", "dialect": "langfuse"}])
```

`memory` keeps spans in process and backs [`observix.testing`](../src/observix/testing.py).

---

## Endpoint resolution

For every provider, in order:

1. `endpoint` in the exporter config
2. The provider's own environment variable
3. The provider's built-in default (local dev, where one makes sense)

The traces path (`/v1/traces`, or Langfuse's `/api/public/otel/v1/traces`) is appended only when your URL has no path of its own. A URL you spell out in full is used verbatim.

## Writing your own

See [extending.md](extending.md). Short version:

```python
from observix.providers import OTLPProviderBase, register_provider


class MyProvider(OTLPProviderBase):
    name = "mybackend"
    default_dialect = "otel_genai"
    endpoint_env = "MYBACKEND_ENDPOINT"

    def build_headers(self, config):
        headers = {"x-api-key": config.options["api_key"]}
        headers.update(config.headers)
        return headers


register_provider("mybackend", MyProvider)
```

Ship it as a package with an entry point and it becomes available by name to everyone, with no change to observix:

```toml
[project.entry-points."observix.providers"]
mybackend = "my_pkg:MyProvider"
```
