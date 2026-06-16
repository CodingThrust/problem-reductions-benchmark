"""
AgentRunner interface and built-in implementations.

AgentRunner is the swap point: the scheduler only talks to this interface,
so swapping in a different agent (opencode, etc.) needs zero scheduler changes.
"""
from abc import ABC, abstractmethod


class AgentRunner(ABC):
    @abstractmethod
    def run(self, ctx, model: str, rule_name: str, per_rule_budget: float) -> dict:
        """Run one bug-hunting session.

        Returns a dict with at minimum:
            rule        str   — rule name
            result      str   — "bug_found" | "no_certificate" | "rejected" | "error:..." | "skipped_budget"
            cost        float — USD spent
            tokens_k    float — tokens used (in thousands)
        """


class FakeRunner(AgentRunner):
    """Returns a canned result — no API calls, no pred calls. For testing only."""

    def __init__(self, cost_per_rule: float = 0.01, result: str = "no_certificate"):
        self.cost_per_rule = cost_per_rule
        self._result = result
        self.call_log: list[tuple[str, str]] = []  # (model, rule) for each real call

    def run(self, ctx, model: str, rule_name: str, per_rule_budget: float) -> dict:
        self.call_log.append((model, rule_name))
        return {
            "rule": rule_name,
            "result": self._result,
            "cost": self.cost_per_rule,
            "tokens_k": 0.5,
        }


class MiniSweRunner(AgentRunner):
    """Wraps benchmark.run_mini.run_one() — the default real agent."""

    def __init__(self, api_base: str | None = None):
        self.api_base = api_base

    def run(self, ctx, model: str, rule_name: str, per_rule_budget: float) -> dict:
        from benchmark.run_mini import run_one  # lazy import — keeps scheduler free of mini-swe deps
        return run_one(model, ctx, rule_name, per_rule_budget, api_base=self.api_base)
