"""Provider endpoint resolution, auth headers, and registry behaviour."""

from __future__ import annotations

import base64

import pytest

from observix.config import ExporterConfig
from observix.errors import ConfigurationError, ProviderNotFoundError
from observix.providers import (
    ArizeProvider,
    ConsoleProvider,
    DatadogProvider,
    LangfuseProvider,
    MemoryProvider,
    MLflowProvider,
    OTLPProvider,
    PhoenixProvider,
    Provider,
    ProviderContext,
    available_providers,
    register_provider,
    resolve_provider,
    unregister_provider,
)
from observix.providers.base import _with_traces_path


class TestTracesPathAppending:
    def test_appends_to_a_bare_host(self) -> None:
        assert _with_traces_path("http://localhost:4318", "/v1/traces") == (
            "http://localhost:4318/v1/traces"
        )

    def test_is_idempotent(self) -> None:
        url = "http://localhost:4318/v1/traces"
        assert _with_traces_path(url, "/v1/traces") == url

    def test_leaves_a_url_that_already_has_a_path_alone(self) -> None:
        """A user who spells out a full endpoint means it."""
        url = "http://collector.internal/custom/ingest"
        assert _with_traces_path(url, "/v1/traces") == url

    def test_strips_a_trailing_slash(self) -> None:
        assert _with_traces_path("http://host:4318/", "/v1/traces") == (
            "http://host:4318/v1/traces"
        )


class TestLangfuseProvider:
    def test_region_selects_the_host(self) -> None:
        provider = LangfuseProvider()
        config = ExporterConfig(provider="langfuse", options={"region": "us"})
        endpoint = provider.resolve_endpoint(config)
        assert endpoint == "https://us.cloud.langfuse.com/api/public/otel/v1/traces"

    def test_defaults_to_the_eu_region(self) -> None:
        endpoint = LangfuseProvider().resolve_endpoint(ExporterConfig(provider="langfuse"))
        assert endpoint.startswith("https://cloud.langfuse.com")

    def test_an_unknown_region_is_reported(self) -> None:
        config = ExporterConfig(provider="langfuse", options={"region": "mars"})
        with pytest.raises(ConfigurationError, match="Unknown Langfuse region"):
            LangfuseProvider().resolve_endpoint(config)

    def test_self_hosted_host_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.internal")
        endpoint = LangfuseProvider().resolve_endpoint(ExporterConfig(provider="langfuse"))
        assert endpoint == "https://langfuse.internal/api/public/otel/v1/traces"

    def test_builds_basic_auth_from_the_key_pair(self) -> None:
        config = ExporterConfig(
            provider="langfuse", options={"public_key": "pk-1", "secret_key": "sk-2"}
        )
        headers = LangfuseProvider().build_headers(config)
        expected = base64.b64encode(b"pk-1:sk-2").decode()
        assert headers["Authorization"] == f"Basic {expected}"

    def test_reads_credentials_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-env")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-env")
        headers = LangfuseProvider().build_headers(ExporterConfig(provider="langfuse"))
        assert "Authorization" in headers

    def test_sets_the_ingestion_version_header(self) -> None:
        config = ExporterConfig(provider="langfuse", options={"public_key": "a", "secret_key": "b"})
        headers = LangfuseProvider().build_headers(config)
        assert headers["x-langfuse-ingestion-version"] == "4"

    def test_missing_credentials_are_reported_actionably(self) -> None:
        with pytest.raises(ConfigurationError, match="LANGFUSE_PUBLIC_KEY"):
            LangfuseProvider().build_headers(ExporterConfig(provider="langfuse"))

    def test_grpc_is_rejected_because_langfuse_does_not_accept_it(self) -> None:
        config = ExporterConfig(
            provider="langfuse",
            protocol="grpc",
            options={"public_key": "a", "secret_key": "b"},
        )
        with pytest.raises(ConfigurationError, match="does not accept OTLP over gRPC"):
            LangfuseProvider().create_exporter(config, ProviderContext())

    def test_the_default_dialect_is_langfuse(self) -> None:
        assert LangfuseProvider.default_dialect == "langfuse"


class TestPhoenixProvider:
    def test_defaults_to_a_local_phoenix(self) -> None:
        endpoint = PhoenixProvider().resolve_endpoint(ExporterConfig(provider="phoenix"))
        assert endpoint == "http://localhost:6006/v1/traces"

    def test_environment_endpoint_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "https://phoenix.internal")
        endpoint = PhoenixProvider().resolve_endpoint(ExporterConfig(provider="phoenix"))
        assert endpoint == "https://phoenix.internal/v1/traces"

    def test_api_key_becomes_a_bearer_token(self) -> None:
        config = ExporterConfig(provider="phoenix", options={"api_key": "k"})
        assert PhoenixProvider().build_headers(config)["authorization"] == "Bearer k"

    def test_project_name_is_sent_as_a_header(self) -> None:
        config = ExporterConfig(provider="phoenix", options={"project_name": "proj"})
        headers = PhoenixProvider().build_headers(config)
        assert headers["x-phoenix-project-name"] == "proj"

    def test_the_default_dialect_is_openinference(self) -> None:
        assert PhoenixProvider.default_dialect == "openinference"

    def test_no_credentials_needed_for_local_use(self) -> None:
        assert PhoenixProvider().build_headers(ExporterConfig(provider="phoenix")) == {}


class TestOTLPProvider:
    def test_reads_the_signal_specific_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://c:4318/v1/traces")
        endpoint = OTLPProvider().resolve_endpoint(ExporterConfig(provider="otlp"))
        assert endpoint == "http://c:4318/v1/traces"

    def test_falls_back_to_the_generic_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
        endpoint = OTLPProvider().resolve_endpoint(ExporterConfig(provider="otlp"))
        assert endpoint == "http://collector:4318/v1/traces"

    def test_honours_standard_otel_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "api-key=abc")
        headers = OTLPProvider().build_headers(ExporterConfig(provider="otlp"))
        assert headers["api-key"] == "abc"

    def test_a_missing_endpoint_is_reported_actionably(self) -> None:
        with pytest.raises(ConfigurationError, match="OTEL_EXPORTER_OTLP"):
            OTLPProvider().require_endpoint(ExporterConfig(provider="otlp"))


class TestOtherProviders:
    def test_mlflow_sends_the_experiment_id(self) -> None:
        config = ExporterConfig(provider="mlflow", options={"experiment_id": "42"})
        assert MLflowProvider().build_headers(config)["x-mlflow-experiment-id"] == "42"

    def test_arize_requires_credentials(self) -> None:
        with pytest.raises(ConfigurationError, match="ARIZE_SPACE_ID"):
            ArizeProvider().build_headers(ExporterConfig(provider="arize"))

    def test_arize_builds_credential_headers(self) -> None:
        config = ExporterConfig(provider="arize", options={"space_id": "s", "api_key": "k"})
        headers = ArizeProvider().build_headers(config)
        assert headers == {"space_id": "s", "api_key": "k"}

    def test_datadog_defaults_to_the_local_agent(self) -> None:
        endpoint = DatadogProvider().resolve_endpoint(ExporterConfig(provider="datadog"))
        assert endpoint == "http://localhost:4318/v1/traces"

    def test_datadog_intake_requires_an_api_key(self) -> None:
        config = ExporterConfig(provider="datadog", options={"site": "datadoghq.com"})
        with pytest.raises(ConfigurationError, match="DD_API_KEY"):
            DatadogProvider().build_headers(config)

    def test_datadog_agent_mode_needs_no_key(self) -> None:
        assert DatadogProvider().build_headers(ExporterConfig(provider="datadog")) == {}

    def test_console_defaults_to_passthrough(self) -> None:
        assert ConsoleProvider.default_dialect == "passthrough"

    def test_memory_reuses_a_supplied_exporter(self) -> None:
        from observix.providers.memory import InMemorySpanExporter

        existing = InMemorySpanExporter()
        config = ExporterConfig(provider="memory", options={"exporter": existing})
        assert MemoryProvider().create_exporter(config, ProviderContext()) is existing


class TestProviderRegistry:
    def test_builtins_are_available(self) -> None:
        names = available_providers()
        for expected in (
            "console",
            "memory",
            "otlp",
            "phoenix",
            "langfuse",
            "mlflow",
            "arize",
            "datadog",
        ):
            assert expected in names

    def test_resolves_by_name(self) -> None:
        assert isinstance(resolve_provider("phoenix"), PhoenixProvider)

    def test_unknown_name_lists_alternatives(self) -> None:
        with pytest.raises(ProviderNotFoundError, match="phoenix"):
            resolve_provider("nope")

    def test_third_party_registration_needs_no_core_changes(self) -> None:
        class CustomProvider(Provider):
            name = "custom"
            default_dialect = "openinference"

            def create_exporter(self, config, context):
                from observix.providers.memory import InMemorySpanExporter

                return InMemorySpanExporter()

        register_provider("custom", CustomProvider)
        try:
            resolved = resolve_provider("custom")
            assert isinstance(resolved, CustomProvider)
            assert resolved.default_dialect == "openinference"
        finally:
            unregister_provider("custom")

    def test_a_registration_can_override_a_builtin(self) -> None:
        class FakePhoenix(Provider):
            name = "phoenix"

            def create_exporter(self, config, context):
                raise NotImplementedError

        register_provider("phoenix", FakePhoenix)
        try:
            assert isinstance(resolve_provider("phoenix"), FakePhoenix)
        finally:
            unregister_provider("phoenix")
        assert isinstance(resolve_provider("phoenix"), PhoenixProvider)
