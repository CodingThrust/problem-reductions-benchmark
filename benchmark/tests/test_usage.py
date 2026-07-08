"""
Tests for benchmark/usage.py — token-usage accounting.

Pins the OpenAI vs Anthropic usage mapping, the 4-bucket sums, and (de)serialization.
"""
from types import SimpleNamespace

from benchmark.usage import (
    Usage,
    extract_usage,
    usage_as_dict,
    usage_from_dict,
    usage_from_response,
)


class TestSerialization:
    def test_usage_roundtrips_through_dict(self):
        u = Usage(input_tokens=10, output_tokens=20, cache_read_tokens=5, cache_write_tokens=3)
        assert usage_from_dict(usage_as_dict(u)) == u

    def test_usage_from_dict_tolerates_missing_and_none(self):
        assert usage_from_dict(None) == Usage()
        assert usage_from_dict({"input": 7}) == Usage(input_tokens=7)


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
