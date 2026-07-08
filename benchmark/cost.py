"""
Authoritative cost accounting for the hard budget cap.

The submitter pays the bill (their own key, their own negotiated rate), so they supply
their model's per-token price. We recompute cost from raw token usage x that price instead
of trusting the model gateway's self-reported dollar figure — which can be stale or plain
wrong (LiteLLM $0-pricing incidents; Anthropic prompt-cache mis-pricing ~10x). Recomputing
from tokens is what turns the $20 budget into a *hard* cap.

For a budget guard, over-counting is safe and under-counting is not, so callers combine
this figure with the gateway's via max() (see run_mini).

Prices are USD per 1,000,000 tokens (the conventional unit). There is deliberately NO
built-in price table: a stale or wrong default would silently mis-meter the budget, so the
price is always submitter-supplied (PRICE_IN / PRICE_OUT) for a real run.
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


def resolve_price(model: str, override: Price | None = None) -> Price | None:
    """Return the submitter-supplied price, or None if none was given.

    Intentionally has no fallback table — `model` is accepted only for a uniform call site.
    None means "no price"; the caller decides what to do (a real run must reject it; see
    run_submission, which requires PRICE_IN/PRICE_OUT)."""
    return override


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


# ── (de)serialization: the 4-bucket token totals + the declared price snapshot ──────
# These travel in the submission so the backend can RE-METER cost as tokens × price
# (zero-trust, mirroring the bug re-verification) instead of trusting a self-reported
# dollar total. Tokens are the reproducible primitive; the declared price (dated by the
# submission's created_at) is a snapshot anyone can swap to recompute under other prices.

def usage_as_dict(u: Usage) -> dict:
    """Serialize a Usage to the 4-bucket dict shape used on rows and the envelope."""
    return {"input": u.input_tokens, "output": u.output_tokens,
            "cache_read": u.cache_read_tokens, "cache_write": u.cache_write_tokens}


def usage_from_dict(d) -> Usage:
    """Parse a 4-bucket dict (or None/missing → all zeros) back into a Usage."""
    return Usage(
        input_tokens=int(_get(d, "input")),
        output_tokens=int(_get(d, "output")),
        cache_read_tokens=int(_get(d, "cache_read")),
        cache_write_tokens=int(_get(d, "cache_write")),
    )


def price_as_dict(p: Price) -> dict:
    """Serialize a Price to a plain dict (USD per 1M tokens, per bucket)."""
    return {"input": p.input, "output": p.output,
            "cache_read": p.cache_read, "cache_write": p.cache_write}


def price_from_dict(d) -> Price | None:
    """Parse a price dict back into a Price, or None if absent (legacy submission)."""
    if not d:
        return None
    return Price(
        input=float(_get(d, "input")),
        output=float(_get(d, "output")),
        cache_read=float(_get(d, "cache_read")),
        cache_write=float(_get(d, "cache_write")),
    )
