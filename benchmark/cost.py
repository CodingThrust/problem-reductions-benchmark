"""
Authoritative cost accounting for the hard budget cap.

The submitter pays the bill (their own key, their own negotiated rate), so they supply
their model's per-token price. We recompute cost from raw token usage x that price instead
of trusting the model gateway's self-reported dollar figure — which can be stale or plain
wrong (LiteLLM $0-pricing incidents; Anthropic prompt-cache mis-pricing ~10x). Recomputing
from tokens is what turns the $20 budget into a *hard* cap.

For a budget guard, over-counting is safe and under-counting is not, so callers combine
this figure with the gateway's via max() (see run_mini).

Prices are USD per 1,000,000 tokens (the conventional unit). DEFAULT_PRICES is a small
convenience table for common models as of 2026-06 — indicative only; the submitter's
--price flags always override it, and any model can be run by supplying its price.
"""
from dataclasses import dataclass

MTOK = 1_000_000


@dataclass(frozen=True)
class Usage:
    """Token counts split into the four disjoint buckets we price separately."""
    input_tokens: int = 0      # uncached prompt tokens
    output_tokens: int = 0     # completion tokens
    cache_read_tokens: int = 0  # prompt tokens served from cache
    cache_write_tokens: int = 0  # prompt tokens written to cache

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_read_tokens + self.cache_write_tokens)


@dataclass(frozen=True)
class Price:
    """Per-1M-token USD rates. When a provider doesn't bill caching separately, leave
    cache_read/cache_write at 0 and fold those tokens into input via extract_usage."""
    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0

    def cost(self, usage: Usage) -> float:
        return (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_read_tokens * self.cache_read
            + usage.cache_write_tokens * self.cache_write
        ) / MTOK


# Indicative public list prices as of 2026-06 (USD / 1M tokens). Override with --price.
DEFAULT_PRICES: dict[str, Price] = {
    "anthropic/claude-opus-4":   Price(15.0, 75.0, 1.5, 18.75),
    "anthropic/claude-sonnet-4": Price(3.0, 15.0, 0.3, 3.75),
    "anthropic/claude-haiku-4":  Price(1.0, 5.0, 0.1, 1.25),
}


def resolve_price(model: str, override: Price | None = None) -> Price | None:
    """Submitter override wins; otherwise the longest matching default prefix; else None
    (caller must then fall back to the gateway figure + step/token backstops)."""
    if override is not None:
        return override
    best, best_len = None, -1
    for prefix, price in DEFAULT_PRICES.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    return best


def _get(obj, key: str, default=0):
    """Read `key` from an object attribute or a dict, treating None/missing as default."""
    if obj is None:
        return default
    val = obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    return default if val is None else val


def usage_from_response(usage) -> Usage:
    """Map one LiteLLM `usage` object (or raw OpenAI/Anthropic shape) to our four buckets.

    LiteLLM normalizes everything OpenAI-style: `prompt_tokens` is the full input and the
    cache split lives under `prompt_tokens_details.{cached_tokens, cache_creation_tokens}`.
    Some raw providers instead report `cache_read_input_tokens` / `cache_creation_input_tokens`
    at the top level — we read both, preferring the details block. `cached_tokens` (cache
    read) is a subset of `prompt_tokens`, so it is subtracted out of the uncached input;
    cache-write is counted as its own bucket (never under-counting, which is the safe side
    for a budget guard)."""
    prompt = int(_get(usage, "prompt_tokens"))
    completion = int(_get(usage, "completion_tokens"))
    details = _get(usage, "prompt_tokens_details", None)
    cached = int(_get(details, "cached_tokens")) if details is not None else 0
    cache_write_details = int(_get(details, "cache_creation_tokens")) if details is not None else 0
    cache_read = cached or int(_get(usage, "cache_read_input_tokens"))
    cache_write = cache_write_details or int(_get(usage, "cache_creation_input_tokens"))
    return Usage(
        input_tokens=max(prompt - cached, 0),  # cache read is a subset of prompt_tokens
        output_tokens=completion,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


def extract_usage(messages: list) -> Usage:
    """Sum per-message usage across an agent trajectory."""
    total = Usage()
    for msg in messages:
        resp = (msg.get("extra", {}) or {}).get("response") if isinstance(msg, dict) else None
        if resp is not None and _get(resp, "usage", None) is not None:
            total = total + usage_from_response(_get(resp, "usage", None))
    return total
