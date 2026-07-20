"""Shared model-API helpers for the benchmark runner and preflight."""
from __future__ import annotations

from pathlib import Path

import yaml

from benchmark.usage import extract_usage

CONFIG_FILE = Path(__file__).with_name("agent_config.yaml")
SKIP_RULES = {"mod", "traits", "graph_helpers", "analysis", "cost", "registry", "graph"}
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MODEL_TIMEOUT_SECONDS = 300
DEFAULT_MODEL_RETRIES = 2


def list_rules(repo_dir: str | Path) -> list[str]:
    """List runnable rules in their canonical filename order."""
    rules_dir = Path(repo_dir) / "src" / "rules"
    return [path.stem for path in sorted(rules_dir.glob("*.rs"))
            if path.stem not in SKIP_RULES]


def message_text(message: dict) -> str:
    parts = [message.get("content") or "", message.get("reasoning_content") or ""]
    return "\n".join(part for part in parts if part)


def session_usage(agent):
    usage = extract_usage(agent.messages)
    total_tokens = usage.total_tokens
    if not total_tokens:
        for message in agent.messages:
            response = message.get("extra", {}).get("response")
            if response and hasattr(response, "usage"):
                total_tokens += getattr(response.usage, "total_tokens", 0)
    return round(total_tokens / 1000, 2), usage


def build_model(model_name: str, api_base: str | None, max_tokens: int,
                *, api_key: str | None = None,
                observation_template: str | None = None,
                format_error_template: str | None = None,
                model_timeout_seconds: int = DEFAULT_MODEL_TIMEOUT_SECONDS,
                model_retries: int = DEFAULT_MODEL_RETRIES):
    from minisweagent.models.litellm_model import LitellmModel

    kwargs = {"timeout": model_timeout_seconds, "num_retries": model_retries}
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


def load_agent_config() -> tuple[dict, dict]:
    """Load the benchmark-owned prompt and model message templates."""
    config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    agent_config = dict(config.get("agent", {}))
    agent_config["cost_limit"] = 0
    return agent_config, config.get("model", {}) or {}
