"""Global runtime state.

Deliberately a module-level singleton. ``@observe`` runs on every instrumented
call, so the enabled check must be one attribute load --- not a lookup through
a registry or a context variable.

The invariant that matters: :attr:`Runtime.enabled` is only ``True`` once a
tracer exists, so the decorator never has to test more than one flag.
"""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opentelemetry.trace import Tracer

if TYPE_CHECKING:  # pragma: no cover
    from opentelemetry.sdk.trace import TracerProvider

    from .config import ObservixConfig
    from .pipeline.builder import Pipeline


@dataclass
class Runtime:
    """Everything the hot path needs, resolved once at ``configure()`` time."""

    enabled: bool = False
    """``True`` only when a tracer is installed and telemetry is switched on."""

    record_content: bool = True
    """Whether any destination retains prompts. Gates content serialisation."""

    tracer: Tracer | None = None
    config: ObservixConfig | None = None
    tracer_provider: TracerProvider | None = None
    pipelines: list[Pipeline] = field(default_factory=list)

    #: Set when observix installed the global tracer provider, so shutdown
    #: knows whether it owns it.
    owns_global_provider: bool = False


_lock = threading.RLock()
_runtime = Runtime()


def runtime() -> Runtime:
    """The current runtime. Hot path --- keep this cheap."""
    return _runtime


def is_enabled() -> bool:
    """Whether telemetry is currently being recorded."""
    return _runtime.enabled


def set_runtime(new_runtime: Runtime) -> Runtime:
    """Atomically replace the runtime, returning the previous one."""
    global _runtime
    with _lock:
        previous = _runtime
        _runtime = new_runtime
        return previous


def reset_runtime() -> Runtime:
    """Restore the disabled default. Returns the previous runtime."""
    return set_runtime(Runtime())


def lock() -> AbstractContextManager[bool]:
    """The configuration lock, for callers that mutate several fields."""
    return _lock
