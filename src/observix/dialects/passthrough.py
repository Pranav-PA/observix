"""The identity dialect: emit canonical ``observix.*`` attributes unchanged.

Useful for debugging a pipeline, for backends you control, and as the baseline
in dialect tests.
"""

from __future__ import annotations

from typing import ClassVar

from .base import CanonicalView, Dialect, TranslationResult


class PassthroughDialect(Dialect):
    """Emit the canonical vocabulary verbatim."""

    name: ClassVar[str] = "passthrough"

    def translate(self, view: CanonicalView) -> TranslationResult:
        return TranslationResult(attributes=dict(view.attributes))
