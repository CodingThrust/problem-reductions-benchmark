"""Whole-repository mini-swe-agent backend."""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from benchmark.env_context import EnvContext
from benchmark.headless import safe_model_label
from benchmark.usage import extract_usage

CONFIG_FILE = Path(__file__).parent / "config.yaml"
SKIP_RULES = {"mod", "traits", "graph_helpers", "analysis", "cost", "registry", "graph"}
DEFAULT_MAX_TOKENS = 8192


def list_rules(repo_dir: str | Path) -> list[str]:
    """List runnable rules; used by preflight to verify a prepared repository."""
    rules_dir = Path(repo_dir) / "src" / "rules"
    return [f.stem for f in sorted(rules_dir.glob("*.rs")) if f.stem not in SKIP_RULES]


def _message_text(msg: dict) -> str:
    parts = [msg.get("content") or "", msg.get("reasoning_content") or ""]
    return "\n".join(part for part in parts if part)


def extract_total_tokens(messages: list) -> int:
    total = 0
    for msg in messages:
        response = msg.get("extra", {}).get("response")
        if response and hasattr(response, "usage"):
            total += getattr(response.usage, "total_tokens", 0)
    return total


def save_trajectory(messages: list, path: Path) -> None:
    """Save a normalized agent history as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for msg in messages:
            handle.write(json.dumps({"role": msg.get("role", ""),
                                     "content": _message_text(msg)}) + "\n")


class TrajectoryWriter:
    """Append-only incremental trajectory writer — live progress during a session.

    Without this, the trajectory only exists after the session ends, so a healthy slow
    run and a dead hang look identical from outside (and a previous crash's leftover
    file masquerades as the current run's progress). flush() appends any new messages
    after each agent step; save_trajectory() still makes the authoritative final write
    at session end, in the identical format.
    """

    def __init__(self, path: Path):
        self._path = path
        self._written = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)  # fresh session — never append onto a stale file

    def flush(self, messages: list) -> None:
        if len(messages) <= self._written:
            return
        with self._path.open("a", encoding="utf-8") as handle:
            for msg in messages[self._written:]:
                handle.write(json.dumps({"role": msg.get("role", ""),
                                         "content": _message_text(msg)}) + "\n")
        self._written = len(messages)


def _session_usage(agent):
    usage = extract_usage(agent.messages)
    total_tokens = usage.total_tokens or extract_total_tokens(agent.messages)
    return round(total_tokens / 1000, 2), usage


def _build_model(model_name: str, api_base: str | None, max_tokens: int,
                 model_kwargs: dict | None = None, api_key: str | None = None,
                 observation_template: str | None = None,
                 format_error_template: str | None = None):
    from minisweagent.models.litellm_model import LitellmModel

    # A hung API call must fail fast and retry — never freeze a whole-repo session
    # indefinitely. User-supplied MODEL_KWARGS still wins on any key.
    kwargs = {"timeout": 300, "num_retries": 2, **dict(model_kwargs or {})}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key
    config = {}
    if observation_template is not None:
        config["observation_template"] = observation_template
    if format_error_template is not None:
        config["format_error_template"] = format_error_template
    return LitellmModel(model_name=model_name, model_kwargs=kwargs, **config)


def _load_agent_config(config_path: str | Path | None, default_file: Path,
                       strategy: str | None) -> tuple[dict, dict, str]:
    """Load prompts while force-disabling mini-swe's hidden cost/step limits."""
    config_file = Path(config_path) if config_path else default_file
    config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    if strategy is None:
        strategy_file = os.environ.get("AGENT_STRATEGY_FILE")
        strategy = Path(strategy_file).read_text(encoding="utf-8") if strategy_file else ""
    agent_config = dict(config.get("agent", {}))
    # 0 means unlimited in mini-swe-agent. The completion command in the prompt is the
    # normal exit; the runner only retains per-command/process timeouts for wedged tools.
    agent_config["step_limit"] = 0
    agent_config["cost_limit"] = 0
    return agent_config, config.get("model", {}) or {}, strategy


def run_repo_session(
    model_name: str,
    ctx: EnvContext,
    *,
    api_base: str | None = None,
    trajectory_dir: Path | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    config_path: str | Path | None = None,
    strategy: str | None = None,
    model_kwargs: dict | None = None,
    api_key: str | None = None,
    submit_session=None,
) -> dict:
    """Run one self-terminating session over the complete repository."""
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment

    agent_config, model_config, strategy = _load_agent_config(
        config_path, CONFIG_FILE, strategy)
    safe_model = safe_model_label(model_name)
    writer = None
    if trajectory_dir is not None:
        trajectory_dir = Path(trajectory_dir).resolve()
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        writer = TrajectoryWriter(trajectory_dir / f"{safe_model}_whole-repo.jsonl")

    class _StepFlushingAgent(DefaultAgent):
        """DefaultAgent that flushes the trajectory after every step (best-effort)."""

        def step(self):
            result = super().step()
            if writer is not None:
                try:
                    writer.flush(self.messages)
                except OSError:
                    pass  # observability must never kill the session
            return result

    agent = _StepFlushingAgent(
        _build_model(
            model_name, api_base, max_tokens, model_kwargs=model_kwargs, api_key=api_key,
            observation_template=model_config.get("observation_template"),
            format_error_template=model_config.get("format_error_template"),
        ),
        LocalEnvironment(),
        **agent_config,
    )
    agent.extra_template_vars = {
        "repo_dir": str(ctx.repo_path),
        "commit_hash": ctx.commit_hash[:7],
        "strategy": strategy,
        "submit_limit": submit_session.limit if submit_session is not None else 0,
    }

    run_error = None
    try:
        agent.run(task="find-bugs")
    except Exception as error:  # salvage verified submissions and partial logs
        run_error = f"{type(error).__name__}: {error}"

    tokens_k, usage = _session_usage(agent)
    if trajectory_dir is not None:
        save_trajectory(agent.messages,
                        trajectory_dir / f"{safe_model}_whole-repo.jsonl")
    return {"tokens_k": tokens_k, "usage": usage, "error": run_error}
