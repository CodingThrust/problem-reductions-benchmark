"""
Tests for benchmark/run_mini.py model construction.

The real minisweagent is stubbed via sys.modules so these run anywhere.
"""
import sys
import types

import pytest

from benchmark.run_mini import _build_model


class _FakeLitellmModel:
    def __init__(self, model_name, model_kwargs, **config):
        self.model_name = model_name
        self.model_kwargs = model_kwargs
        self.config = config


@pytest.fixture
def _fake_litellm(monkeypatch):
    fake = types.ModuleType("minisweagent.models.litellm_model")
    fake.LitellmModel = _FakeLitellmModel
    monkeypatch.setitem(sys.modules, "minisweagent.models.litellm_model", fake)


class TestBuildModel:
    def test_defaults_timeout_and_retries(self, _fake_litellm):
        """A hung API call must fail fast, not freeze the whole-repo session."""
        model = _build_model("openai/x", None, 8192)
        assert model.model_kwargs["timeout"] == 300
        assert model.model_kwargs["num_retries"] == 2

    def test_user_kwargs_override_defaults(self, _fake_litellm):
        model = _build_model("openai/x", None, 8192, model_kwargs={"timeout": 60})
        assert model.model_kwargs["timeout"] == 60
        assert model.model_kwargs["num_retries"] == 2

    def test_endpoint_config_passthrough(self, _fake_litellm):
        model = _build_model("openai/x", "https://api.example/v1", 4096, api_key="k")
        assert model.model_kwargs["api_base"] == "https://api.example/v1"
        assert model.model_kwargs["api_key"] == "k"
        assert model.model_kwargs["max_tokens"] == 4096
