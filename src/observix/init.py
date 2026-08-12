"""Lifecycle: configure, flush, shut down.

Everything that differs between backends lives here and in
:mod:`observix.config`. Application code --- the ``@observe`` decorators and
span setters --- never changes when a destination does.
"""

from __future__ import annotations

import atexit
import contextlib
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opentelemetry import trace as trace_api

from ._version import __version__
from .config import ExporterConfig, ObservixConfig, build_config
from .errors import logger
from .pipeline.builder import build_tracer_provider
from .state import Runtime, reset_runtime, runtime, set_runtime

INSTRUMENTATION_SCOPE = "observix"

_configure_lock = threading.RLock()
_atexit_registered = False


def configure(
    *,
    service_name: str | None = None,
    exporters: Sequence[str | ExporterConfig | dict] | None = None,
    service_version: str | None = None,
    environment: str | None = None,
    sample_ratio: float | None = None,
    capture_content: bool | None = None,
    redact: Any | None = None,
    enabled: bool | None = None,
    adopt_foreign: bool | None = None,
    resource_attributes: dict | None = None,
    batch: dict | None = None,
    set_global_tracer_provider: bool | None = None,
    config_file: str | Path | None = None,
    config: ObservixConfig | None = None,
) -> ObservixConfig:
    """Configure observix. Call once, early, before instrumented code runs.

    Every argument is optional; anything omitted falls back to the config file,
    then the environment, then a built-in default. Passing ``config=`` supplies
    a fully-built configuration and skips the other layers.

    Calling this again reconfigures cleanly, shutting the previous pipelines
    down first.

    Args:
        service_name: Logical service name recorded on every span.
        exporters: Destinations --- provider names, mappings, or
            :class:`~observix.config.ExporterConfig` objects.
        service_version: Version recorded on every span.
        environment: Deployment environment (``production``, ``staging``, ...).
        sample_ratio: Global head-sampling ratio, ``0.0`` to ``1.0``.
        capture_content: Master switch for recording prompts and completions.
        redact: Default redaction policy for destinations without their own.
        enabled: Set ``False`` to configure without recording.
        resource_attributes: Extra OTel resource attributes.
        batch: Default ``BatchSpanProcessor`` options for all destinations.
        set_global_tracer_provider: Register with OpenTelemetry globally.
        config_file: Explicit path to a config file.
        config: A pre-built configuration, bypassing all other layers.

    Returns:
        The effective configuration.

    Raises:
        ConfigurationError: If the configuration is invalid.
    """
    with _configure_lock:
        effective = config or build_config(
            config_file=config_file,
            service_name=service_name,
            exporters=list(exporters) if exporters is not None else None,
            service_version=service_version,
            environment=environment,
            sample_ratio=sample_ratio,
            capture_content=capture_content,
            redact=redact,
            enabled=enabled,
            adopt_foreign=adopt_foreign,
            resource_attributes=resource_attributes,
            batch=batch,
            set_global_tracer_provider=set_global_tracer_provider,
        )

        _shutdown_current(flush=True)

        if not effective.enabled:
            logger.debug("observix: disabled by configuration.")
            set_runtime(Runtime(enabled=False, config=effective))
            return effective

        if not effective.active_exporters:
            logger.warning(
                "observix: no exporters configured, so nothing will be recorded. "
                "Pass exporters=[...] or set OBSERVIX_EXPORTERS."
            )
            set_runtime(Runtime(enabled=False, config=effective))
            return effective

        provider, pipelines = build_tracer_provider(effective)

        owns_global = False
        if effective.set_global_tracer_provider:
            owns_global = _install_global_provider(provider)

        tracer = provider.get_tracer(INSTRUMENTATION_SCOPE, __version__)

        set_runtime(
            Runtime(
                enabled=True,
                record_content=effective.records_content(),
                tracer=tracer,
                config=effective,
                tracer_provider=provider,
                pipelines=pipelines,
                owns_global_provider=owns_global,
            )
        )

        _register_atexit()
        logger.info(
            "observix %s active: service=%r destinations=[%s]",
            __version__,
            effective.service_name,
            ", ".join(f"{p.name}->{p.dialect.name}" for p in pipelines),
        )
        return effective


def _install_global_provider(provider: Any) -> bool:
    """Install the global tracer provider, tolerating one already being set.

    Returns whether observix now owns the global provider.
    """
    existing = trace_api.get_tracer_provider()
    # A ProxyTracerProvider means nothing real has been installed yet.
    if type(existing).__name__ not in ("ProxyTracerProvider", "NoOpTracerProvider"):
        logger.warning(
            "observix: a global TracerProvider (%s) is already installed, so "
            "observix will trace through its own provider instead. Pass "
            "set_global_tracer_provider=False to silence this.",
            type(existing).__name__,
        )
        return False
    trace_api.set_tracer_provider(provider)
    return True


def _register_atexit() -> None:
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_atexit_shutdown)
        _atexit_registered = True


def _atexit_shutdown() -> None:  # pragma: no cover - interpreter teardown
    with contextlib.suppress(Exception):
        shutdown()


def flush(timeout_millis: int = 30_000) -> bool:
    """Force every destination to export what it has buffered.

    Call before a short-lived process exits --- a script, a serverless
    invocation, a test --- so buffered spans are not lost.

    Returns:
        ``True`` if every destination flushed within the timeout.
    """
    current = runtime()
    if current.tracer_provider is None:
        return True
    try:
        return bool(current.tracer_provider.force_flush(timeout_millis))
    except Exception:
        logger.warning("observix: flush failed", exc_info=True)
        return False


def shutdown(*, flush_first: bool = True) -> None:
    """Flush and tear down every pipeline. Safe to call more than once."""
    with _configure_lock:
        _shutdown_current(flush=flush_first)
        reset_runtime()


def _shutdown_current(*, flush: bool) -> None:
    """Tear down the active runtime's provider, if any."""
    current = runtime()
    provider = current.tracer_provider
    if provider is None:
        return
    try:
        if flush:
            provider.force_flush(5_000)
    except Exception:
        logger.debug("observix: flush during shutdown failed", exc_info=True)
    try:
        provider.shutdown()
    except Exception:
        logger.debug("observix: provider shutdown failed", exc_info=True)


def get_config() -> ObservixConfig | None:
    """The effective configuration, or ``None`` if never configured."""
    return runtime().config


def get_pipelines() -> list[Any]:
    """The active destination pipelines. Useful for diagnostics and tests."""
    return list(runtime().pipelines)


def is_configured() -> bool:
    """Whether observix is configured and recording."""
    return runtime().enabled
