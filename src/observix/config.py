"""Configuration: the only thing that changes when you switch backends.

Layered, lowest precedence first:

1. Built-in defaults
2. A config file --- ``observix.toml``, or ``[tool.observix]`` in ``pyproject.toml``
3. Environment variables
4. Keyword arguments to :func:`observix.configure`

Each layer overrides the previous *field by field*, so setting one environment
variable does not discard everything the file said.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import ConfigurationError, logger
from .model.enums import RedactionMode
from .redaction import RedactionPolicy, coerce_policy

ENV_PREFIX = "OBSERVIX_"

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}

#: Filenames searched, in order, when no explicit config path is given.
CONFIG_FILENAMES = ("observix.toml", "pyproject.toml")


@dataclass
class ExporterConfig:
    """One destination.

    Attributes:
        provider: Registered provider name (``phoenix``, ``langfuse``, ``otlp``, ...).
        name: Identifier for this destination. Defaults to ``provider``; set it
            explicitly to configure the same provider twice.
        dialect: Override the provider's default dialect.
        endpoint: Override the provider's default endpoint.
        headers: Extra headers merged over whatever the provider builds.
        protocol: ``http/protobuf``, ``http/json``, or ``grpc``, when supported.
        timeout: Export timeout in seconds.
        redact: This destination's privacy policy.
        sample_ratio: Fraction of traces this destination receives.
        batch: Overrides for the ``BatchSpanProcessor`` (``max_queue_size``,
            ``schedule_delay_millis``, ``max_export_batch_size``,
            ``export_timeout_millis``).
        options: Provider-specific settings.
        enabled: Set ``False`` to configure a destination without activating it.
    """

    provider: str
    name: str | None = None
    dialect: str | None = None
    endpoint: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    protocol: str | None = None
    timeout: float | None = None
    redact: str | RedactionPolicy | Mapping[str, Any] | None = None
    sample_ratio: float = 1.0
    batch: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.provider or not isinstance(self.provider, str):
            raise ConfigurationError("ExporterConfig.provider must be a non-empty string.")
        if self.name is None:
            self.name = self.provider
        if not 0.0 <= self.sample_ratio <= 1.0:
            raise ConfigurationError(
                f"Exporter {self.name!r}: sample_ratio must be between 0.0 and 1.0, "
                f"got {self.sample_ratio}."
            )

    def redaction_policy(self) -> RedactionPolicy:
        """Resolve :attr:`redact` into a policy object."""
        try:
            return coerce_policy(self.redact)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"Exporter {self.name!r}: {exc}") from exc

    @property
    def key(self) -> str:
        """Stable identifier for this destination."""
        return self.name or self.provider


@dataclass
class ObservixConfig:
    """Complete observix configuration."""

    enabled: bool = True
    service_name: str = "unknown_service"
    service_version: str | None = None
    environment: str | None = None
    exporters: list[ExporterConfig] = field(default_factory=list)

    sample_ratio: float = 1.0
    """Global head sampling. Applied once, before any destination sees a span."""

    capture_content: bool = True
    """Master switch for recording prompts, completions and tool payloads."""

    redact: str | RedactionPolicy | Mapping[str, Any] | None = None
    """Default policy, applied to destinations that do not set their own."""

    adopt_foreign: bool = False
    """Map inbound OpenLLMetry / OpenInference / MLflow spans onto the canonical
    model, so spans from other instrumentation libraries also fan out and get
    redacted. See :mod:`observix.integrations.adopt`."""

    resource_attributes: dict[str, Any] = field(default_factory=dict)
    batch: dict[str, Any] = field(default_factory=dict)
    propagate: bool = True
    """Install observix's propagator as the global one."""

    set_global_tracer_provider: bool = True
    """Register with OpenTelemetry globally. Disable to stay isolated."""

    def __post_init__(self) -> None:
        if not 0.0 <= self.sample_ratio <= 1.0:
            raise ConfigurationError(
                f"sample_ratio must be between 0.0 and 1.0, got {self.sample_ratio}."
            )
        self.exporters = [_coerce_exporter(e) for e in self.exporters]
        _reject_duplicate_names(self.exporters)

    @property
    def active_exporters(self) -> list[ExporterConfig]:
        return [e for e in self.exporters if e.enabled]

    def records_content(self) -> bool:
        """Whether any active destination will retain recorded content.

        When ``False``, span creation skips content serialisation entirely ---
        the reason a fully-redacted deployment costs nearly nothing.
        """
        if not self.capture_content:
            return False
        active = self.active_exporters
        if not active:
            return False
        default = coerce_policy(self.redact)
        for exporter in active:
            policy = exporter.redaction_policy() if exporter.redact is not None else default
            if not policy.drops_content:
                return True
        return False

    def merge(self, **overrides: Any) -> ObservixConfig:
        """Return a copy with ``overrides`` applied, ignoring ``None`` values."""
        clean = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **clean) if clean else self


# --- Coercion ----------------------------------------------------------------


def _coerce_exporter(value: Any) -> ExporterConfig:
    """Accept an ``ExporterConfig``, a provider name, or a mapping."""
    if isinstance(value, ExporterConfig):
        return value
    if isinstance(value, str):
        return ExporterConfig(provider=value)
    if isinstance(value, Mapping):
        data = dict(value)
        provider = data.pop("provider", None) or data.pop("type", None)
        if not provider:
            raise ConfigurationError(f"Exporter entry {value!r} is missing a 'provider' key.")
        known = {f.name for f in ExporterConfig.__dataclass_fields__.values()}
        options = data.pop("options", {}) or {}
        extra = {k: data.pop(k) for k in list(data) if k not in known}
        if extra:
            options = {**extra, **options}
        return ExporterConfig(provider=str(provider), options=dict(options), **data)
    raise ConfigurationError(
        f"Cannot build an exporter from {type(value).__name__}; "
        "expected a name, a mapping, or an ExporterConfig."
    )


def _reject_duplicate_names(exporters: Sequence[ExporterConfig]) -> None:
    seen: dict[str, int] = {}
    for exporter in exporters:
        seen[exporter.key] = seen.get(exporter.key, 0) + 1
    duplicates = sorted(name for name, count in seen.items() if count > 1)
    if duplicates:
        raise ConfigurationError(
            f"Duplicate exporter name(s): {', '.join(duplicates)}. "
            "Give each destination a distinct 'name' when reusing a provider."
        )


# --- File loading ------------------------------------------------------------


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        # Standard library from 3.11; falls back to tomli on 3.10.
        import tomllib  # type: ignore[import-not-found,unused-ignore]
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10 only
        try:
            import tomli as tomllib  # type: ignore[no-redef,import-not-found,unused-ignore]
        except ModuleNotFoundError:
            logger.warning(
                "observix: cannot read %s because no TOML parser is available. "
                "Install with:  pip install 'observix[toml]'",
                path,
            )
            return {}
    try:
        with open(path, "rb") as handle:
            return dict(tomllib.load(handle))
    except Exception as exc:
        raise ConfigurationError(f"Failed to parse {path}: {exc}") from exc


def find_config_file(start: Path | None = None) -> Path | None:
    """Search ``start`` and its ancestors for a config file."""
    directory = (start or Path.cwd()).resolve()
    for candidate_dir in (directory, *directory.parents):
        for filename in CONFIG_FILENAMES:
            path = candidate_dir / filename
            if path.is_file():
                if filename == "pyproject.toml":
                    data = _load_toml(path)
                    if "observix" not in data.get("tool", {}):
                        continue
                return path
    return None


def load_config_file(path: str | Path | None = None) -> dict[str, Any]:
    """Read the ``[observix]`` table from a config file.

    Returns an empty dict when no file is found.
    """
    resolved = Path(path) if path is not None else find_config_file()
    if resolved is None:
        return {}
    if not resolved.is_file():
        raise ConfigurationError(f"Config file not found: {resolved}")

    data = _load_toml(resolved)
    if resolved.name == "pyproject.toml":
        section = data.get("tool", {}).get("observix", {})
    else:
        section = data.get("observix", data)

    if not isinstance(section, dict):
        raise ConfigurationError(f"{resolved}: [observix] must be a table.")
    return _normalize_file_section(dict(section))


def _normalize_file_section(section: dict[str, Any]) -> dict[str, Any]:
    """Turn the file's exporter table into a list of exporter mappings."""
    raw = section.pop("exporters", None)
    if raw is None:
        return section

    exporters: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        # [observix.exporters.phoenix] -> name "phoenix", provider "phoenix"
        for name, body in raw.items():
            entry = dict(body) if isinstance(body, Mapping) else {}
            entry.setdefault("provider", entry.pop("type", name))
            entry.setdefault("name", name)
            exporters.append(entry)
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, str):
                exporters.append({"provider": item})
            elif isinstance(item, Mapping):
                exporters.append(dict(item))
            else:
                raise ConfigurationError(f"Invalid exporter entry: {item!r}")
    else:
        raise ConfigurationError("'exporters' must be a table or a list.")

    section["exporters"] = exporters
    return section


# --- Environment -------------------------------------------------------------


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigurationError(f"{name}={raw!r} is not a boolean.")


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name}={raw!r} is not a number.") from exc


def parse_headers(raw: str) -> dict[str, str]:
    """Parse ``key=value,key2=value2``, the OTLP header convention."""
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ConfigurationError(f"Malformed header {pair!r}; expected 'key=value'.")
        key, _, value = pair.partition("=")
        headers[key.strip()] = value.strip()
    return headers


def load_env_config() -> dict[str, Any]:
    """Read global settings from :envvar:`OBSERVIX_*` variables."""
    out: dict[str, Any] = {}

    for key, env in (
        ("service_name", "SERVICE_NAME"),
        ("service_version", "SERVICE_VERSION"),
        ("environment", "ENVIRONMENT"),
        ("redact", "REDACT"),
    ):
        value = os.environ.get(ENV_PREFIX + env)
        if value:
            out[key] = value

    for key, env in (
        ("enabled", "ENABLED"),
        ("capture_content", "CAPTURE_CONTENT"),
        ("adopt_foreign", "ADOPT_FOREIGN"),
    ):
        value_bool = _env_bool(ENV_PREFIX + env)
        if value_bool is not None:
            out[key] = value_bool

    ratio = _env_float(ENV_PREFIX + "SAMPLE_RATIO")
    if ratio is not None:
        out["sample_ratio"] = ratio

    raw_exporters = os.environ.get(ENV_PREFIX + "EXPORTERS")
    if raw_exporters:
        out["exporters"] = [
            {"provider": part.strip()} for part in raw_exporters.split(",") if part.strip()
        ]

    # OTel's own service-name variable, honoured when ours is unset.
    if "service_name" not in out:
        otel_service = os.environ.get("OTEL_SERVICE_NAME")
        if otel_service:
            out["service_name"] = otel_service

    return out


def apply_exporter_env(exporter: ExporterConfig) -> ExporterConfig:
    """Overlay ``OBSERVIX_<NAME>_*`` variables onto one exporter."""
    prefix = f"{ENV_PREFIX}{exporter.key.upper().replace('-', '_')}_"

    endpoint = os.environ.get(prefix + "ENDPOINT")
    if endpoint:
        exporter.endpoint = endpoint

    dialect = os.environ.get(prefix + "DIALECT")
    if dialect:
        exporter.dialect = dialect

    protocol = os.environ.get(prefix + "PROTOCOL")
    if protocol:
        exporter.protocol = protocol

    redact = os.environ.get(prefix + "REDACT")
    if redact:
        exporter.redact = redact

    headers = os.environ.get(prefix + "HEADERS")
    if headers:
        exporter.headers = {**exporter.headers, **parse_headers(headers)}

    ratio = _env_float(prefix + "SAMPLE_RATIO")
    if ratio is not None:
        if not 0.0 <= ratio <= 1.0:
            raise ConfigurationError(
                f"{prefix}SAMPLE_RATIO must be between 0.0 and 1.0, got {ratio}."
            )
        exporter.sample_ratio = ratio

    enabled = _env_bool(prefix + "ENABLED")
    if enabled is not None:
        exporter.enabled = enabled

    timeout = _env_float(prefix + "TIMEOUT")
    if timeout is not None:
        exporter.timeout = timeout

    return exporter


# --- Assembly ----------------------------------------------------------------


def build_config(*, config_file: str | Path | None = None, **overrides: Any) -> ObservixConfig:
    """Assemble the effective configuration from all four layers."""
    merged: dict[str, Any] = {}
    merged.update(load_config_file(config_file))
    merged.update(load_env_config())
    merged.update({k: v for k, v in overrides.items() if v is not None})

    known = set(ObservixConfig.__dataclass_fields__)
    unknown = sorted(set(merged) - known)
    if unknown:
        raise ConfigurationError(
            f"Unknown configuration option(s): {', '.join(unknown)}. "
            f"Valid options: {', '.join(sorted(known))}."
        )

    if isinstance(merged.get("redact"), str):
        RedactionMode.coerce(merged["redact"])  # validate early, fail loudly

    config = ObservixConfig(**merged)
    config.exporters = [apply_exporter_env(e) for e in config.exporters]
    return config
