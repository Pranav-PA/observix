"""Configuration assembly, layering, and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from observix.config import (
    ExporterConfig,
    ObservixConfig,
    build_config,
    load_config_file,
    parse_headers,
)
from observix.errors import ConfigurationError
from observix.model.enums import RedactionMode


class TestExporterConfig:
    def test_name_defaults_to_provider(self) -> None:
        assert ExporterConfig(provider="phoenix").key == "phoenix"

    def test_name_can_be_overridden(self) -> None:
        assert ExporterConfig(provider="otlp", name="tempo").key == "tempo"

    def test_rejects_an_out_of_range_ratio(self) -> None:
        with pytest.raises(ConfigurationError, match="between 0.0 and 1.0"):
            ExporterConfig(provider="otlp", sample_ratio=1.5)

    def test_rejects_an_empty_provider(self) -> None:
        with pytest.raises(ConfigurationError):
            ExporterConfig(provider="")

    def test_redaction_string_becomes_a_policy(self) -> None:
        policy = ExporterConfig(provider="otlp", redact="hashed").redaction_policy()
        assert policy.mode is RedactionMode.HASHED

    def test_invalid_redaction_mode_is_reported_with_the_exporter_name(self) -> None:
        with pytest.raises(ConfigurationError, match="langfuse"):
            ExporterConfig(provider="langfuse", redact="nonsense").redaction_policy()


class TestObservixConfig:
    def test_coerces_exporter_names_to_configs(self) -> None:
        config = ObservixConfig(exporters=["phoenix", "langfuse"])
        assert [e.provider for e in config.exporters] == ["phoenix", "langfuse"]

    def test_coerces_mappings(self) -> None:
        config = ObservixConfig(exporters=[{"provider": "otlp", "sample_ratio": 0.5}])
        assert config.exporters[0].sample_ratio == 0.5

    def test_unknown_mapping_keys_become_provider_options(self) -> None:
        config = ObservixConfig(exporters=[{"provider": "langfuse", "region": "us"}])
        assert config.exporters[0].options["region"] == "us"

    def test_rejects_duplicate_names(self) -> None:
        with pytest.raises(ConfigurationError, match="Duplicate exporter"):
            ObservixConfig(exporters=["phoenix", "phoenix"])

    def test_the_same_provider_twice_is_fine_with_distinct_names(self) -> None:
        config = ObservixConfig(
            exporters=[
                {"provider": "otlp", "name": "tempo"},
                {"provider": "otlp", "name": "honeycomb"},
            ]
        )
        assert len(config.exporters) == 2

    def test_disabled_exporters_are_excluded_from_active(self) -> None:
        config = ObservixConfig(
            exporters=[{"provider": "otlp"}, {"provider": "phoenix", "enabled": False}]
        )
        assert [e.provider for e in config.active_exporters] == ["otlp"]


class TestRecordsContent:
    """The flag that decides whether span creation serialises prompts at all."""

    def test_true_when_a_destination_retains_content(self) -> None:
        assert ObservixConfig(exporters=["memory"]).records_content() is True

    def test_false_when_every_destination_drops_content(self) -> None:
        config = ObservixConfig(exporters=[{"provider": "memory", "redact": "none"}])
        assert config.records_content() is False

    def test_true_when_only_some_destinations_drop_content(self) -> None:
        config = ObservixConfig(
            exporters=[
                {"provider": "memory", "name": "a", "redact": "none"},
                {"provider": "memory", "name": "b"},
            ]
        )
        assert config.records_content() is True

    def test_false_when_capture_content_is_off(self) -> None:
        config = ObservixConfig(exporters=["memory"], capture_content=False)
        assert config.records_content() is False

    def test_false_with_no_exporters(self) -> None:
        assert ObservixConfig().records_content() is False


class TestEnvironmentLayer:
    def test_reads_global_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSERVIX_SERVICE_NAME", "svc")
        monkeypatch.setenv("OBSERVIX_ENVIRONMENT", "staging")
        monkeypatch.setenv("OBSERVIX_SAMPLE_RATIO", "0.5")
        config = build_config()
        assert config.service_name == "svc"
        assert config.environment == "staging"
        assert config.sample_ratio == 0.5

    def test_reads_the_exporter_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSERVIX_EXPORTERS", "phoenix, langfuse")
        config = build_config()
        assert [e.provider for e in config.exporters] == ["phoenix", "langfuse"]

    def test_per_exporter_overrides_are_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSERVIX_EXPORTERS", "langfuse")
        monkeypatch.setenv("OBSERVIX_LANGFUSE_SAMPLE_RATIO", "0.25")
        monkeypatch.setenv("OBSERVIX_LANGFUSE_REDACT", "hashed")
        monkeypatch.setenv("OBSERVIX_LANGFUSE_ENDPOINT", "https://lf.internal")
        exporter = build_config().exporters[0]
        assert exporter.sample_ratio == 0.25
        assert exporter.redact == "hashed"
        assert exporter.endpoint == "https://lf.internal"

    def test_code_arguments_beat_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSERVIX_SERVICE_NAME", "from-env")
        assert build_config(service_name="from-code").service_name == "from-code"

    def test_otel_service_name_is_honoured_as_a_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OTEL_SERVICE_NAME", "otel-svc")
        assert build_config().service_name == "otel-svc"

    def test_a_non_boolean_flag_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBSERVIX_ENABLED", "perhaps")
        with pytest.raises(ConfigurationError, match="not a boolean"):
            build_config()

    def test_an_out_of_range_per_exporter_ratio_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OBSERVIX_EXPORTERS", "otlp")
        monkeypatch.setenv("OBSERVIX_OTLP_SAMPLE_RATIO", "5")
        with pytest.raises(ConfigurationError, match="between 0.0 and 1.0"):
            build_config()


class TestConfigFile:
    def test_reads_observix_toml(self, tmp_path: Path) -> None:
        (tmp_path / "observix.toml").write_text(
            """
[observix]
service_name = "from-file"
sample_ratio = 0.75

[observix.exporters.phoenix]
sample_ratio = 0.5

[observix.exporters.langfuse]
redact = "hashed"
""",
            encoding="utf-8",
        )
        config = build_config(config_file=tmp_path / "observix.toml")
        assert config.service_name == "from-file"
        assert config.sample_ratio == 0.75

        by_name = {e.key: e for e in config.exporters}
        assert by_name["phoenix"].sample_ratio == 0.5
        assert by_name["langfuse"].redact == "hashed"

    def test_reads_pyproject_tool_section(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
[tool.observix]
service_name = "from-pyproject"
exporters = ["console"]
""",
            encoding="utf-8",
        )
        config = build_config(config_file=tmp_path / "pyproject.toml")
        assert config.service_name == "from-pyproject"
        assert config.exporters[0].provider == "console"

    def test_environment_beats_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "observix.toml").write_text(
            '[observix]\nservice_name = "from-file"\n', encoding="utf-8"
        )
        monkeypatch.setenv("OBSERVIX_SERVICE_NAME", "from-env")
        config = build_config(config_file=tmp_path / "observix.toml")
        assert config.service_name == "from-env"

    def test_a_missing_explicit_file_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="not found"):
            load_config_file(tmp_path / "absent.toml")

    def test_malformed_toml_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "observix.toml"
        path.write_text("[observix\nbroken", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="Failed to parse"):
            load_config_file(path)

    def test_a_list_of_exporters_is_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "observix.toml"
        path.write_text('[observix]\nexporters = ["console", "memory"]\n', encoding="utf-8")
        config = build_config(config_file=path)
        assert [e.provider for e in config.exporters] == ["console", "memory"]


class TestMisc:
    def test_unknown_options_are_rejected_with_a_list_of_valid_ones(self) -> None:
        with pytest.raises(ConfigurationError, match="service_name"):
            build_config(nonsense_option=True)

    def test_parse_headers(self) -> None:
        assert parse_headers("a=1,b=2") == {"a": "1", "b": "2"}
        assert parse_headers(" a = 1 , b = 2 ") == {"a": "1", "b": "2"}

    def test_malformed_headers_are_reported(self) -> None:
        with pytest.raises(ConfigurationError, match="Malformed header"):
            parse_headers("novalue")
