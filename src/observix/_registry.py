"""Generic entry-point-backed plugin registry.

Shared by the dialect and provider registries. Third-party packages register
plugins by declaring an entry point; nothing in observix's core needs to change
to support a new backend.

Discovery is lazy and happens at most once. A plugin that fails to import is
logged and skipped --- one broken third-party package must not prevent the rest
of the pipeline from starting.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

from .errors import logger

T = TypeVar("T")


def _iter_entry_points(group: str) -> Iterator[tuple[str, Callable[[], object]]]:
    """Yield ``(name, loader)`` for every entry point in ``group``."""
    from importlib import metadata

    entries = metadata.entry_points(group=group)

    for entry in entries:
        yield entry.name, entry.load


class Registry(Generic[T]):
    """A name-to-plugin registry with lazy entry-point discovery.

    Explicit registrations always take precedence over discovered ones, so a
    user can override a built-in by registering their own under the same name.
    """

    def __init__(self, group: str, kind: str) -> None:
        self._group = group
        self._kind = kind
        self._lock = threading.RLock()
        self._explicit: dict[str, T] = {}
        self._discovered: dict[str, T] = {}
        self._discovery_done = False

    def register(self, name: str, plugin: T, *, override: bool = True) -> None:
        """Register a plugin under ``name``."""
        key = _normalize(name)
        with self._lock:
            if not override and key in self._explicit:
                return
            self._explicit[key] = plugin

    def unregister(self, name: str) -> None:
        """Remove an explicit registration. Used by tests."""
        with self._lock:
            self._explicit.pop(_normalize(name), None)

    def get(self, name: str) -> T | None:
        """Resolve a plugin by name, or ``None`` if unknown."""
        key = _normalize(name)
        with self._lock:
            if key in self._explicit:
                return self._explicit[key]
        self._discover()
        with self._lock:
            return self._discovered.get(key)

    def names(self) -> list[str]:
        """All known plugin names, explicit and discovered."""
        self._discover()
        with self._lock:
            return sorted(set(self._explicit) | set(self._discovered))

    def _discover(self) -> None:
        """Load entry points once, tolerating individual failures."""
        with self._lock:
            if self._discovery_done:
                return
            self._discovery_done = True

        found: dict[str, T] = {}
        try:
            for name, load in _iter_entry_points(self._group):
                try:
                    found[_normalize(name)] = load()  # type: ignore[assignment]
                except Exception:
                    logger.warning(
                        "observix: failed to load %s plugin %r from entry point group %r",
                        self._kind,
                        name,
                        self._group,
                        exc_info=True,
                    )
        except Exception:
            logger.warning(
                "observix: %s plugin discovery failed for group %r",
                self._kind,
                self._group,
                exc_info=True,
            )

        with self._lock:
            self._discovered.update(found)

    def reset(self) -> None:
        """Clear all state, forcing rediscovery. Used by tests."""
        with self._lock:
            self._explicit.clear()
            self._discovered.clear()
            self._discovery_done = False


def _normalize(name: str) -> str:
    """Plugin names are case-insensitive and treat ``-`` and ``_`` alike."""
    return name.strip().lower().replace("-", "_")
