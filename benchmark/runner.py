"""
AgentRunner interface and built-in implementations.

AgentRunner is the swap point: the scheduler only talks to this interface,
so swapping in a different agent (opencode, etc.) needs zero scheduler changes.
"""
from abc import ABC, abstractmethod


class AgentRunner(ABC):
    @abstractmethod
    def run(self, ctx, model: str, rule_name: str) -> dict:
        """Run one bug-hunting session.

        Returns a dict with at minimum:
            rule        str   — rule name
            result      str   — "bug_found" | "no_certificate" | "rejected" | "error:..."
            tokens_k    float — tokens used (in thousands)
        """


class FakeRunner(AgentRunner):
    """Returns a canned result — no API calls, no pred calls. For testing only."""

    def __init__(self, result: str = "no_certificate"):
        self._result = result
        self.call_log: list[tuple[str, str]] = []  # (model, rule) for each real call

    def run(self, ctx, model: str, rule_name: str) -> dict:
        self.call_log.append((model, rule_name))
        return {
            "rule": rule_name,
            "result": self._result,
            "tokens_k": 0.5,
        }


class MiniSweRunner(AgentRunner):
    """Wraps benchmark.run_mini.run_one() — the default real agent."""

    def __init__(self, api_base: str | None = None, max_tokens: int | None = None,
                 config_path=None, strategy: str | None = None,
                 model_kwargs: dict | None = None, api_key: str | None = None):
        self.api_base = api_base
        self.max_tokens = max_tokens
        self.config_path = config_path  # hand-editable prompt override (None → bundled config)
        self.strategy = strategy        # extra hints injected into the prompt's {{strategy}} slot
        self.model_kwargs = model_kwargs  # arbitrary litellm passthrough for non-standard providers
        self.api_key = api_key            # generic key (no provider-specific env var name needed)

    def run(self, ctx, model: str, rule_name: str) -> dict:
        from benchmark.run_mini import DEFAULT_MAX_TOKENS, run_one  # lazy — keep scheduler mini-swe-free
        return run_one(model, ctx, rule_name, api_base=self.api_base,
                       max_tokens=self.max_tokens if self.max_tokens is not None else DEFAULT_MAX_TOKENS,
                       config_path=self.config_path, strategy=self.strategy,
                       model_kwargs=self.model_kwargs, api_key=self.api_key)
