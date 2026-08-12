"""Backend vocabulary translation.

A dialect is a pure function from observix's canonical model to one backend's
native attribute names, applied at export time so a single recorded span can
arrive natively-shaped at several backends at once.
"""

from .base import CanonicalView, Dialect, TranslationResult
from .langfuse import LangfuseDialect
from .mlflow import MLflowDialect
from .openinference import OpenInferenceDialect
from .otel_genai import OTelGenAIDialect
from .passthrough import PassthroughDialect
from .registry import (
    available_dialects,
    register_dialect,
    resolve_dialect,
    unregister_dialect,
)

__all__ = [
    "CanonicalView",
    "Dialect",
    "LangfuseDialect",
    "MLflowDialect",
    "OTelGenAIDialect",
    "OpenInferenceDialect",
    "PassthroughDialect",
    "TranslationResult",
    "available_dialects",
    "register_dialect",
    "resolve_dialect",
    "unregister_dialect",
]
