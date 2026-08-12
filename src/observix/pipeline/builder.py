"""Assemble a ``TracerProvider`` and one independent pipeline per destination.

Each destination gets its own chain:

    FilteringSpanProcessor -> BatchSpanProcessor -> DialectSpanExporter -> exporter

Independence is the point. Each destination has its own queue and its own
worker thread, so a slow or failing backend cannot stall another, and each has
its own dialect, redaction policy and sampling ratio.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, Sampler, TraceIdRatioBased

from .._version import __version__
from ..config import ExporterConfig, ObservixConfig
from ..dialects.base import Dialect
from ..dialects.registry import resolve_dialect
from ..errors import ConfigurationError, logger
from ..providers.base import Provider, ProviderContext
from ..providers.registry import resolve_provider
from ..redaction import RedactionPolicy, coerce_policy
from .filtering_processor import FilteringSpanProcessor
from .translating_exporter import DialectSpanExporter


@dataclass
class Pipeline:
    """One fully-wired destination."""

    name: str
    provider: Provider
    dialect: Dialect
    redaction: RedactionPolicy
    exporter: SpanExporter
    """The destination's real exporter, unwrapped."""
    translating_exporter: DialectSpanExporter
    processor: FilteringSpanProcessor
    config: ExporterConfig

    def __repr__(self) -> str:
        return (
            f"<Pipeline {self.name!r} provider={self.provider.name!r} "
            f"dialect={self.dialect.name!r} ratio={self.processor.sample_ratio}>"
        )


def build_resource(config: ObservixConfig) -> Resource:
    """Build the OTel resource describing this service."""
    attributes: dict[str, Any] = {
        "service.name": config.service_name,
        "telemetry.sdk.name": "observix",
        "telemetry.sdk.version": __version__,
    }
    if config.service_version:
        attributes["service.version"] = config.service_version
    if config.environment:
        attributes["deployment.environment.name"] = config.environment
        # Older backends still key off the pre-1.27 attribute name.
        attributes["deployment.environment"] = config.environment
    attributes.update(config.resource_attributes)
    return Resource.create(attributes)


def build_sampler(config: ObservixConfig) -> Sampler:
    """Global head sampler. Per-destination ratios are applied separately."""
    if config.sample_ratio >= 1.0:
        return ALWAYS_ON
    return ParentBased(root=TraceIdRatioBased(config.sample_ratio))


def build_pipeline(
    exporter_config: ExporterConfig,
    *,
    context: ProviderContext,
    default_redaction: RedactionPolicy,
    default_batch: dict[str, Any] | None = None,
    capture_content: bool = True,
    adopt_foreign: bool = False,
) -> Pipeline:
    """Wire one destination end to end."""
    provider = resolve_provider(exporter_config.provider)

    dialect_name = exporter_config.dialect or provider.default_dialect
    dialect_kwargs: dict[str, Any] = dict(exporter_config.options.get("dialect_options", {}))
    if not capture_content:
        dialect_kwargs.setdefault("capture_content", False)
    dialect = _build_dialect(dialect_name, dialect_kwargs)

    redaction = (
        exporter_config.redaction_policy()
        if exporter_config.redact is not None
        else default_redaction
    )

    exporter = provider.create_exporter(exporter_config, context)

    translating = DialectSpanExporter(
        exporter,
        dialect,
        redaction=redaction,
        destination=exporter_config.key,
        adopt_foreign=bool(exporter_config.options.get("adopt_foreign", adopt_foreign)),
        resource_overrides=provider.resource_overrides(exporter_config),
    )

    batch_kwargs = {**(default_batch or {}), **exporter_config.batch}
    batch = BatchSpanProcessor(translating, **_valid_batch_kwargs(batch_kwargs))

    processor = FilteringSpanProcessor(
        batch,
        sample_ratio=exporter_config.sample_ratio,
        predicate=exporter_config.options.get("predicate"),
    )

    return Pipeline(
        name=exporter_config.key,
        provider=provider,
        dialect=dialect,
        redaction=redaction,
        exporter=exporter,
        translating_exporter=translating,
        processor=processor,
        config=exporter_config,
    )


def _build_dialect(name: str, kwargs: dict[str, Any]) -> Dialect:
    """Resolve a dialect, retrying without kwargs it does not accept."""
    try:
        return resolve_dialect(name, **kwargs)
    except TypeError:
        # A custom dialect need not accept capture_content.
        if kwargs:
            return resolve_dialect(name)
        raise


_BATCH_KEYS = frozenset(
    {
        "max_queue_size",
        "schedule_delay_millis",
        "max_export_batch_size",
        "export_timeout_millis",
    }
)


def _valid_batch_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(kwargs) - _BATCH_KEYS)
    if unknown:
        raise ConfigurationError(
            f"Unknown batch option(s): {', '.join(unknown)}. "
            f"Valid options: {', '.join(sorted(_BATCH_KEYS))}."
        )
    return kwargs


def build_tracer_provider(
    config: ObservixConfig,
) -> tuple[TracerProvider, list[Pipeline]]:
    """Build the tracer provider and every configured pipeline.

    A destination that fails to build is skipped with a warning rather than
    taking down the rest --- a missing Langfuse key should not cost you Phoenix.
    :class:`ConfigurationError` from the *first* destination is still raised,
    so a single misconfigured exporter is not silently ignored.
    """
    provider = TracerProvider(
        resource=build_resource(config),
        sampler=build_sampler(config),
    )

    context = ProviderContext(
        service_name=config.service_name,
        service_version=config.service_version,
        environment=config.environment,
        resource_attributes=dict(config.resource_attributes),
    )
    default_redaction = coerce_policy(config.redact)

    pipelines: list[Pipeline] = []
    failures: list[tuple[str, Exception]] = []

    for exporter_config in config.active_exporters:
        try:
            pipeline = build_pipeline(
                exporter_config,
                context=context,
                default_redaction=default_redaction,
                default_batch=config.batch,
                capture_content=config.capture_content,
                adopt_foreign=config.adopt_foreign,
            )
        except Exception as build_error:
            failures.append((exporter_config.key, build_error))
            logger.error(
                "observix: destination %r could not be configured and was skipped: %s",
                exporter_config.key,
                build_error,
            )
            continue
        provider.add_span_processor(pipeline.processor)
        pipelines.append(pipeline)

    if failures and not pipelines:
        # Every destination failed, so there is no telemetry at all. Surface it
        # rather than starting up silently dead.
        first_name, first_error = failures[0]
        raise ConfigurationError(
            f"No destinations could be configured. First failure ({first_name}): {first_error}"
        ) from first_error

    return provider, pipelines
