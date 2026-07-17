"""Whole-repository agent backend interface."""
from abc import ABC, abstractmethod


class AgentRunner(ABC):
    @abstractmethod
    def run_repo(self, ctx, model: str, *, trajectory_dir=None) -> dict:
        """Run one self-terminating repository session."""


class FakeRunner(AgentRunner):
    """No-model smoke runner used by tests and Docker build validation."""

    def run_repo(self, ctx, model: str, *, trajectory_dir=None) -> dict:
        return {"rows": [], "tokens_k": 0.0, "trajectory": [],
                "usage": None, "error": None}


class MiniSweRunner(AgentRunner):
    def __init__(self, api_base: str | None = None, max_tokens: int | None = None,
                 config_path=None, strategy: str | None = None,
                 model_kwargs: dict | None = None, api_key: str | None = None,
                 submit_session=None):
        self.api_base = api_base
        self.max_tokens = max_tokens
        self.config_path = config_path
        self.strategy = strategy
        self.model_kwargs = model_kwargs
        self.api_key = api_key
        self.submit_session = submit_session

    def run_repo(self, ctx, model: str, *, trajectory_dir=None) -> dict:
        from benchmark.run_mini import DEFAULT_MAX_TOKENS, run_repo_session
        return run_repo_session(
            model, ctx, api_base=self.api_base,
            max_tokens=self.max_tokens if self.max_tokens is not None else DEFAULT_MAX_TOKENS,
            trajectory_dir=trajectory_dir, config_path=self.config_path,
            strategy=self.strategy, model_kwargs=self.model_kwargs, api_key=self.api_key,
            submit_session=self.submit_session,
        )


class _HeadlessRunner(AgentRunner):
    def __init__(self, config_path=None, strategy: str | None = None,
                 api_key: str | None = None, submit_session=None):
        self.config_path = config_path
        self.strategy = strategy
        self.api_key = api_key
        self.submit_session = submit_session

    @staticmethod
    def _run_repo():
        raise NotImplementedError

    def run_repo(self, ctx, model: str, *, trajectory_dir=None) -> dict:
        return self._run_repo()(
            model, ctx, trajectory_dir=trajectory_dir, config_path=self.config_path,
            strategy=self.strategy, api_key=self.api_key,
            submit_session=self.submit_session,
        )


class ClaudeCodeRunner(_HeadlessRunner):
    @staticmethod
    def _run_repo():
        from benchmark.claude_code import run_repo_claude
        return run_repo_claude


class CodexRunner(_HeadlessRunner):
    @staticmethod
    def _run_repo():
        from benchmark.codex_cli import run_repo_codex
        return run_repo_codex
