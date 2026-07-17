"""Whole-repository Claude Code backend using self-terminating ``claude -p``."""
from __future__ import annotations

import json
import os
from pathlib import Path

from benchmark.headless import child_env, load_rendered_prompts, run_headless_session
from benchmark.run_mini import CONFIG_FILE
from benchmark.usage import Usage, usage_from_response

DEFAULT_ALLOWED_TOOLS = "Bash,Read,Grep,Glob"
# This is only a hung-process backstop, not an agent turn budget.
DEFAULT_SESSION_TIMEOUT = 6 * 3600


def _block_text(block: dict) -> str:
    block_type = block.get("type")
    if block_type == "text":
        return block.get("text") or ""
    if block_type == "thinking":
        return block.get("thinking") or ""
    if block_type == "tool_use":
        payload = block.get("input") or {}
        if isinstance(payload.get("command"), str):
            return f"[tool_use {block.get('name', '?')}]\n{payload['command']}"
        return f"[tool_use {block.get('name', '?')}] {json.dumps(payload, sort_keys=True)}"
    if block_type == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            return "\n".join(_block_text(item) for item in content if isinstance(item, dict))
        return content if isinstance(content, str) else ""
    return ""


def parse_stream(lines, *, collect_trajectory: bool = True) -> dict:
    trajectory: list[dict] = []
    usage = Usage()
    seen_message_ids: set = set()
    result_event = None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type in ("assistant", "user"):
            message = event.get("message") or {}
            if collect_trajectory:
                content = message.get("content")
                text = (content if isinstance(content, str) else
                        "\n".join(filter(None, (_block_text(block) for block in (content or [])
                                                 if isinstance(block, dict)))))
                trajectory.append({"role": event_type, "content": text})
            message_id = message.get("id")
            if (event_type == "assistant" and message.get("usage")
                    and message_id not in seen_message_ids):
                seen_message_ids.add(message_id)
                usage = usage + usage_from_response(message["usage"])
        elif event_type == "result":
            result_event = event
    if result_event and isinstance(result_event.get("usage"), dict):
        cumulative = usage_from_response(result_event["usage"])
        if cumulative.total_tokens:
            usage = cumulative
    steps = int(result_event.get("num_turns") or 0) if result_event else 0
    return {"trajectory": trajectory, "usage": usage, "steps": steps,
            "result_event": result_event}


def _build_command(claude_bin: str, system_prompt: str, task_prompt: str,
                   model: str, allowed_tools: str) -> list[str]:
    bare_model = model.split("/", 1)[1] if model.startswith("anthropic/") else model
    return [
        claude_bin, "-p", task_prompt,
        "--system-prompt", system_prompt,
        "--model", bare_model,
        "--tools", allowed_tools.replace(",", " "),
        "--allowedTools", allowed_tools,
        "--strict-mcp-config",
        "--output-format", "stream-json",
        "--verbose",
    ]


def _child_env(ctx, api_key: str | None) -> dict:
    env = child_env(ctx)
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    if api_key and not env.get("CLAUDE_CODE_OAUTH_TOKEN") and not env.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = api_key
    return env


def run_repo_claude(
    model_name: str,
    ctx,
    *,
    trajectory_dir: Path | None = None,
    config_path: str | Path | None = None,
    strategy: str | None = None,
    api_key: str | None = None,
    claude_bin: str | None = None,
    allowed_tools: str | None = None,
    session_timeout: int | None = None,
    submit_session=None,
) -> dict:
    claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
    allowed_tools = allowed_tools or os.environ.get("CLAUDE_CODE_TOOLS", DEFAULT_ALLOWED_TOOLS)
    session_timeout = session_timeout or int(
        os.environ.get("CLAUDE_CODE_SESSION_TIMEOUT", DEFAULT_SESSION_TIMEOUT))
    variables = {
        "repo_dir": str(ctx.repo_path),
        "commit_hash": ctx.commit_hash[:7],
        "submit_limit": submit_session.limit if submit_session is not None else 0,
    }
    system_prompt, task_prompt = load_rendered_prompts(
        config_path, CONFIG_FILE, strategy, variables)
    command = _build_command(
        claude_bin, system_prompt, task_prompt, model_name, allowed_tools)
    return run_headless_session(
        command=command, model_name=model_name,
        env=_child_env(ctx, api_key), timeout=session_timeout,
        parser=parse_stream, label="claude", trajectory_dir=trajectory_dir,
        submit_session=submit_session)
