"""Token usage and cost value objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenUsage:
    """Token counts for a single model call.

    ``total_tokens`` is derived when not supplied explicitly, because providers
    disagree about whether the total includes reasoning or cached tokens; an
    explicit value from the provider always wins.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None

    def resolved_total(self) -> int | None:
        """Return the total, deriving it from input + output when absent."""
        if self.total_tokens is not None:
            return self.total_tokens
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def is_empty(self) -> bool:
        return all(
            value is None
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.total_tokens,
                self.reasoning_tokens,
                self.cache_read_input_tokens,
                self.cache_write_input_tokens,
            )
        )


@dataclass(frozen=True)
class Cost:
    """Monetary cost of a model call, in US dollars."""

    input_usd: float | None = None
    output_usd: float | None = None
    total_usd: float | None = None

    def resolved_total(self) -> float | None:
        if self.total_usd is not None:
            return self.total_usd
        if self.input_usd is None and self.output_usd is None:
            return None
        return (self.input_usd or 0.0) + (self.output_usd or 0.0)

    def is_empty(self) -> bool:
        return self.input_usd is None and self.output_usd is None and self.total_usd is None
