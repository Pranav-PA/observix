"""Built-in price book.

Prices are **USD per million tokens** and are inevitably a snapshot. They exist
so cost attribution works out of the box, not as a billing source of truth ---
providers change prices without notice and observix is not in the pricing
business.

Override or extend at runtime:

    from observix.cost import register_price, ModelPrice
    register_price("my-model", ModelPrice(input=1.0, output=3.0))

or point :envvar:`OBSERVIX_PRICES_FILE` at a JSON file of the same shape.
Matching is exact first, then longest-prefix, so ``gpt-4o-2024-11-20``
resolves via the ``gpt-4o`` entry.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens for one model."""

    input: float
    output: float
    cache_read: float | None = None
    """Price for tokens served from the prompt cache, when discounted."""
    cache_write: float | None = None
    """Price for tokens written to the prompt cache, when surcharged."""


#: Keys are matched exactly, then by longest prefix.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    # --- Anthropic ---
    "claude-opus-4": ModelPrice(15.0, 75.0, cache_read=1.5, cache_write=18.75),
    "claude-sonnet-4": ModelPrice(3.0, 15.0, cache_read=0.3, cache_write=3.75),
    "claude-haiku-4": ModelPrice(1.0, 5.0, cache_read=0.1, cache_write=1.25),
    "claude-3-5-haiku": ModelPrice(0.8, 4.0, cache_read=0.08, cache_write=1.0),
    "claude-3-5-sonnet": ModelPrice(3.0, 15.0, cache_read=0.3, cache_write=3.75),
    "claude-3-opus": ModelPrice(15.0, 75.0),
    "claude-3-haiku": ModelPrice(0.25, 1.25),
    # --- OpenAI ---
    "gpt-4o-mini": ModelPrice(0.15, 0.6, cache_read=0.075),
    "gpt-4o": ModelPrice(2.5, 10.0, cache_read=1.25),
    "gpt-4-turbo": ModelPrice(10.0, 30.0),
    "gpt-4": ModelPrice(30.0, 60.0),
    "gpt-3.5-turbo": ModelPrice(0.5, 1.5),
    "o3-mini": ModelPrice(1.1, 4.4, cache_read=0.55),
    "o3": ModelPrice(2.0, 8.0),
    "o1-mini": ModelPrice(1.1, 4.4),
    "o1": ModelPrice(15.0, 60.0),
    "text-embedding-3-small": ModelPrice(0.02, 0.0),
    "text-embedding-3-large": ModelPrice(0.13, 0.0),
    # --- Google ---
    "gemini-2.0-flash": ModelPrice(0.1, 0.4),
    "gemini-1.5-pro": ModelPrice(1.25, 5.0),
    "gemini-1.5-flash": ModelPrice(0.075, 0.3),
    # --- Meta / Mistral / Cohere ---
    "llama-3.1-405b": ModelPrice(2.7, 2.7),
    "llama-3.1-70b": ModelPrice(0.35, 0.4),
    "llama-3.1-8b": ModelPrice(0.05, 0.08),
    "mistral-large": ModelPrice(2.0, 6.0),
    "mistral-small": ModelPrice(0.2, 0.6),
    "command-r-plus": ModelPrice(2.5, 10.0),
    "command-r": ModelPrice(0.15, 0.6),
}
