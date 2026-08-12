"""Exception hierarchy and the fail-open guard.

observix follows one hard rule: **instrumentation must never break the
application**. Every boundary between user code and observix internals is
wrapped in :func:`suppress_and_log`, which swallows unexpected errors, logs
them once per call-site, and lets the caller carry on.

Configuration errors are the deliberate exception -- they surface at
:func:`observix.configure` time, loudly, because they are programmer errors
that are cheap to fix and expensive to silently ignore.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger("observix")

#: When truthy, internal errors are re-raised instead of suppressed. Intended
#: for observix's own test-suite and for users debugging a custom plugin.
_STRICT_ENV = "OBSERVIX_STRICT"


class ObservixError(Exception):
    """Base class for every error raised by observix."""


class ConfigurationError(ObservixError):
    """Raised when configuration is invalid or self-contradictory.

    Deliberately *not* suppressed: surfaced at ``configure()`` time.
    """


class ProviderNotFoundError(ConfigurationError):
    """Raised when a named provider cannot be resolved from the registry."""

    def __init__(self, name: str, available: list[str] | None = None) -> None:
        hint = f" Available providers: {', '.join(sorted(available))}." if available else ""
        super().__init__(f"Unknown provider {name!r}.{hint}")
        self.name = name


class DialectNotFoundError(ConfigurationError):
    """Raised when a named dialect cannot be resolved from the registry."""

    def __init__(self, name: str, available: list[str] | None = None) -> None:
        hint = f" Available dialects: {', '.join(sorted(available))}." if available else ""
        super().__init__(f"Unknown dialect {name!r}.{hint}")
        self.name = name


class MissingDependencyError(ConfigurationError):
    """Raised when a provider needs an optional dependency that is not installed."""

    def __init__(self, provider: str, package: str, extra: str) -> None:
        super().__init__(
            f"Provider {provider!r} requires the {package!r} package. "
            f"Install it with:  pip install 'observix[{extra}]'"
        )
        self.provider = provider
        self.package = package
        self.extra = extra


def strict_mode() -> bool:
    """Return whether internal errors should be re-raised rather than suppressed."""
    return os.environ.get(_STRICT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


#: Call-sites that have already logged, so a hot loop cannot flood the log.
_warned: set[str] = set()


@contextmanager
def suppress_and_log(where: str) -> Iterator[None]:
    """Swallow any exception raised inside the block.

    The first failure at a given ``where`` logs at WARNING with a traceback;
    subsequent failures at the same site log at DEBUG. :class:`ConfigurationError`
    and anything raised while :envvar:`OBSERVIX_STRICT` is set propagate.
    """
    try:
        yield
    except ConfigurationError:
        raise
    except Exception:
        if strict_mode():
            raise
        if where in _warned:
            logger.debug("observix: suppressed error in %s", where, exc_info=True)
        else:
            _warned.add(where)
            logger.warning(
                "observix: suppressed error in %s (further occurrences log at DEBUG). "
                "Telemetry may be incomplete; your application is unaffected.",
                where,
                exc_info=True,
            )


def reset_warned_sites() -> None:
    """Clear the deduplication set. Used by tests."""
    _warned.clear()
