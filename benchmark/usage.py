"""
Token-usage accounting.

Tokens are the reproducible primitive a run reports: raw per-bucket counts summed from the
agent trajectory. There is no dollar, step, or turn budget; the leaderboard's efficiency
metric is bugs per 1K tokens.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Usage:
    """Token counts split into the four disjoint buckets providers report separately."""
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
    cache-write is counted as its own bucket."""
    # Raw Anthropic events use input_tokens/output_tokens instead of the OpenAI names.
    if _get(usage, "prompt_tokens", None) is None and _get(usage, "input_tokens", None) is not None:
        return Usage(
            input_tokens=int(_get(usage, "input_tokens")),
            output_tokens=int(_get(usage, "output_tokens")),
            cache_read_tokens=int(_get(usage, "cache_read_input_tokens")),
            cache_write_tokens=int(_get(usage, "cache_creation_input_tokens")),
        )

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
