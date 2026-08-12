"""Attribute vocabularies.

:mod:`canonical` is observix's own namespace --- the only one application code
writes. The others describe the vocabularies observix *translates into*, one
module per target ecosystem.
"""

from . import canonical, genai, langfuse, mlflow, openinference

__all__ = ["canonical", "genai", "langfuse", "mlflow", "openinference"]
