"""Token-to-dollar cost computation."""

from __future__ import annotations

import json
import os
import re
import threading

from ..errors import suppress_and_log
from ..model.usage import Cost, TokenUsage
from .prices import DEFAULT_PRICES, ModelPrice

_PRICES_FILE_ENV = "OBSERVIX_PRICES_FILE"

_lock = threading.RLock()
_prices: dict[str, ModelPrice] = dict(DEFAULT_PRICES)
_loaded_from_file = False

#: Strips vendor routing prefixes and deployment suffixes so that, for example,
#: ``anthropic/claude-sonnet-4-20250514`` and ``us.anthropic.claude-sonnet-4-v1``
#: both resolve to the ``claude-sonnet-4`` entry.
_PREFIX_RE = re.compile(
    r"^(?:[a-z0-9_-]+/)*"  # provider/route prefixes: "anthropic/", "openrouter/openai/"
    r"(?:(?:us|eu|apac|global)\.)?"  # Bedrock regional prefixes
    r"(?:(?:anthropic|openai|meta|mistral|cohere|google|amazon)\.)?"  # Bedrock vendor
)
_SUFFIX_RE = re.compile(r"[:@-](?:v\d+(?::\d+)?|\d{8}|\d{4}-\d{2}-\d{2}|latest)$")


def register_price(model: str, price: ModelPrice) -> None:
    """Register or override the price for a model key."""
    with _lock:
        _prices[_normalize(model)] = price


def register_prices(prices: dict[str, ModelPrice]) -> None:
    """Register several prices at once."""
    with _lock:
        for model, price in prices.items():
            _prices[_normalize(model)] = price


def get_price(model: str) -> ModelPrice | None:
    """Look up a price by exact match, then by longest matching prefix."""
    _ensure_file_loaded()
    key = _normalize(model)
    with _lock:
        price = _prices.get(key)
        if price is not None:
            return price
        # Longest prefix wins, so "gpt-4o-mini" beats "gpt-4o" for "gpt-4o-mini-x".
        best: ModelPrice | None = None
        best_len = 0
        for candidate, candidate_price in _prices.items():
            if key.startswith(candidate) and len(candidate) > best_len:
                best, best_len = candidate_price, len(candidate)
        return best


def reset_prices() -> None:
    """Restore the built-in price book. Used by tests."""
    global _loaded_from_file
    with _lock:
        _prices.clear()
        _prices.update(DEFAULT_PRICES)
        _loaded_from_file = False


def compute_cost(*, model: str, usage: TokenUsage, provider: str | None = None) -> Cost | None:
    """Compute cost in USD, or ``None`` when the model cannot be priced.

    Cached-token pricing is applied when the price book supplies it: cache reads
    are billed at the discounted rate and removed from the billable input count.
    """
    price = get_price(model)
    if price is None:
        return None

    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0
    cache_read = usage.cache_read_input_tokens or 0
    cache_write = usage.cache_write_input_tokens or 0

    # Providers report cache reads inside the input count; do not bill twice.
    billable_input = (
        max(input_tokens - cache_read, 0) if price.cache_read is not None else input_tokens
    )

    input_usd = billable_input * price.input / 1_000_000
    if price.cache_read is not None and cache_read:
        input_usd += cache_read * price.cache_read / 1_000_000
    if price.cache_write is not None and cache_write:
        input_usd += cache_write * price.cache_write / 1_000_000

    output_usd = output_tokens * price.output / 1_000_000

    # Reasoning tokens are billed as output; providers vary on whether they are
    # already included in output_tokens, so only add when they clearly are not.
    reasoning = usage.reasoning_tokens or 0
    if reasoning and output_tokens and reasoning > output_tokens:
        output_usd = reasoning * price.output / 1_000_000

    return Cost(
        input_usd=round(input_usd, 10),
        output_usd=round(output_usd, 10),
        total_usd=round(input_usd + output_usd, 10),
    )


def _normalize(model: str) -> str:
    """Reduce a vendor model identifier to a price-book key."""
    key = model.strip().lower()
    key = _PREFIX_RE.sub("", key, count=1)
    prev = None
    while prev != key:
        prev = key
        key = _SUFFIX_RE.sub("", key)
    return key


def _ensure_file_loaded() -> None:
    """Load :envvar:`OBSERVIX_PRICES_FILE` on first lookup, at most once."""
    global _loaded_from_file
    if _loaded_from_file:
        return
    with _lock:
        # Double-checked locking: another thread may have loaded while we waited.
        # mypy narrows the flag from the check above and calls this unreachable.
        if _loaded_from_file:
            return  # type: ignore[unreachable]
        _loaded_from_file = True
        path = os.environ.get(_PRICES_FILE_ENV)
        if not path:
            return
        with suppress_and_log("cost._ensure_file_loaded"):
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            for model, entry in raw.items():
                if isinstance(entry, dict) and "input" in entry and "output" in entry:
                    _prices[_normalize(model)] = ModelPrice(
                        input=float(entry["input"]),
                        output=float(entry["output"]),
                        cache_read=_opt_float(entry.get("cache_read")),
                        cache_write=_opt_float(entry.get("cache_write")),
                    )


def _opt_float(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]
