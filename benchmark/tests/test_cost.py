"""
Tests for benchmark/cost.py — authoritative token-based cost accounting.

The point of this module is that we never trust the gateway's dollar figure, so these
tests pin the token→USD math, the OpenAI vs Anthropic usage mapping, and price resolution.
"""
from types import SimpleNamespace

from benchmark.cost import (
    Price,
    Usage,
    extract_usage,
    resolve_price,
    usage_from_response,
)


class TestPriceCost:
    def test_basic_input_output(self):
        # 1M input @ $3, 0.5M output @ $15 = 3 + 7.5
        p = Price(3.0, 15.0)
        assert p.cost(Usage(input_tokens=1_000_000, output_tokens=500_000)) == 10.5

    def test_cache_buckets_priced_separately(self):
        p = Price(3.0, 15.0, cache_read=0.3, cache_write=3.75)
        u = Usage(0, 0, cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
        assert abs(p.cost(u) - (0.3 + 3.75)) < 1e-9

    def test_zero_usage_zero_cost(self):
        assert Price(3.0, 15.0).cost(Usage()) == 0.0


class TestUsage:
    def test_add_and_total(self):
        u = Usage(1, 2, 3, 4) + Usage(10, 20, 30, 40)
        assert (u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens) \
            == (11, 22, 33, 44)
        assert u.total_tokens == 110


class TestUsageFromResponse:
    def test_openai_style_cached_is_subset_of_prompt(self):
        # OpenAI: prompt_tokens INCLUDES cached; we must subtract to avoid double counting.
        usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=200,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=300))
        u = usage_from_response(usage)
        assert u.input_tokens == 700 and u.cache_read_tokens == 300
        assert u.output_tokens == 200 and u.cache_write_tokens == 0

    def test_litellm_normalized_cache_in_details(self):
        # LiteLLM (the actual runner path): cache split lives under prompt_tokens_details.
        usage = SimpleNamespace(
            prompt_tokens=1000, completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=300, cache_creation_tokens=120))
        u = usage_from_response(usage)
        assert u.input_tokens == 700 and u.cache_read_tokens == 300
        assert u.cache_write_tokens == 120 and u.output_tokens == 50

    def test_raw_anthropic_top_level_cache_fallback(self):
        # Raw provider shape: cache read/write at the top level (fallback path).
        usage = SimpleNamespace(prompt_tokens=500, completion_tokens=120,
                                cache_read_input_tokens=400, cache_creation_input_tokens=50)
        u = usage_from_response(usage)
        assert u.input_tokens == 500 and u.cache_read_tokens == 400
        assert u.cache_write_tokens == 50 and u.output_tokens == 120

    def test_dict_form(self):
        u = usage_from_response({"prompt_tokens": 100, "completion_tokens": 10})
        assert u.input_tokens == 100 and u.output_tokens == 10

    def test_missing_fields_default_zero(self):
        u = usage_from_response(SimpleNamespace(prompt_tokens=42))
        assert u.input_tokens == 42 and u.output_tokens == 0


class TestResolvePrice:
    def test_override_wins(self):
        ov = Price(1.0, 2.0)
        assert resolve_price("anthropic/claude-sonnet-4-6", ov) is ov

    def test_no_override_is_none(self):
        # No built-in table by design — price must always be supplied for a real run.
        assert resolve_price("anthropic/claude-sonnet-4-6") is None
        assert resolve_price("some/unknown-model") is None


class TestExtractUsage:
    def _msg(self, **usage_fields):
        return {"extra": {"response": SimpleNamespace(usage=SimpleNamespace(**usage_fields))}}

    def test_sums_across_messages(self):
        msgs = [self._msg(prompt_tokens=100, completion_tokens=10),
                self._msg(prompt_tokens=200, completion_tokens=20),
                {"role": "user", "content": "no response here"}]
        u = extract_usage(msgs)
        assert u.input_tokens == 300 and u.output_tokens == 30

    def test_empty_trajectory(self):
        assert extract_usage([]).total_tokens == 0
