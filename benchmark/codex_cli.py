"""Whole-repository Codex backend using self-terminating ``codex exec``."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import nullcontext
from pathlib import Path

from benchmark.headless import child_env, load_rendered_prompts, run_process
from benchmark.naming import safe_model_label
from benchmark.run_mini import CONFIG_FILE, save_trajectory
from benchmark.usage import Usage

DEFAULT_SANDBOX = "workspace-write"
# This is only a hung-process backstop, not an agent turn budget.
DEFAULT_SESSION_TIMEOUT = 6 * 3600


def _bare_model(model: str) -> str:
    return model.split("/", 1)[1] if model.startswith("openai/") else model


def _build_command(codex_bin: str, prompt: str, model: str,
                   sandbox: str = DEFAULT_SANDBOX) -> list[str]:
    return [
        codex_bin, "exec", "--json", "--ephemeral",
        "--sandbox", sandbox,
        "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
        "--model", _bare_model(model),
        prompt,
    ]


def _child_env(ctx, api_key: str | None) -> dict:
    return child_env(ctx, api_key, api_key_var="OPENAI_API_KEY")


def parse_stream(lines) -> dict:
    trajectory: list[dict] = []
    usage = Usage()
    steps = 0
    thread_id = None
    result_event = None
    for raw in lines:
        try:
            event = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
        elif event_type == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                trajectory.append({"role": "assistant", "content": item["text"]})
            elif item.get("type") == "command_execution":
                steps += 1
                content = f"[command]\n{item.get('command') or ''}"
                output = item.get("aggregated_output") or item.get("output") or ""
                if output:
                    content += f"\n[output]\n{output}"
                trajectory.append({"role": "tool", "content": content})
        elif event_type == "turn.completed":
            result_event = event
            raw_usage = event.get("usage") or {}
            total_input = int(raw_usage.get("input_tokens") or 0)
            cached = int(raw_usage.get("cached_input_tokens") or 0)
            usage = Usage(input_tokens=max(total_input - cached, 0),
                          output_tokens=int(raw_usage.get("output_tokens") or 0),
                          cache_read_tokens=cached)
        elif event_type in ("turn.failed", "error"):
            result_event = event
    return {"trajectory": trajectory, "usage": usage, "steps": steps,
            "thread_id": thread_id, "result_event": result_event}


def _event_error(event: dict | None) -> str | None:
    if not event or event.get("type") not in ("turn.failed", "error"):
        return None
    detail = event.get("error") or event.get("message") or event
    if isinstance(detail, dict):
        detail = json.dumps(detail, sort_keys=True)
    return f"codex {event['type']}: {detail}"


def run_repo_codex(
    model_name: str,
    ctx,
    *,
    trajectory_dir: Path | None = None,
    config_path: str | Path | None = None,
    strategy: str | None = None,
    api_key: str | None = None,
    codex_bin: str | None = None,
    sandbox: str | None = None,
    session_timeout: int | None = None,
    submit_session=None,
) -> dict:
    codex_bin = codex_bin or os.environ.get("CODEX_BIN", "codex")
    sandbox = sandbox or os.environ.get("CODEX_SANDBOX", DEFAULT_SANDBOX)
    session_timeout = session_timeout or int(
        os.environ.get("CODEX_SESSION_TIMEOUT", DEFAULT_SESSION_TIMEOUT))
    variables = {
        "repo_dir": str(ctx.repo_path),
        "commit_hash": ctx.commit_hash[:7],
        "submit_limit": submit_session.limit if submit_session is not None else 0,
    }
    system, task = load_rendered_prompts(config_path, CONFIG_FILE, strategy, variables)
    command = _build_command(codex_bin, f"{system.rstrip()}\n\n{task.lstrip()}",
                             model_name, sandbox)

    safe_model = safe_model_label(model_name)
    stream_log = None
    if trajectory_dir is not None:
        trajectory_dir = Path(trajectory_dir).resolve()
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        stream_log = trajectory_dir / f"{safe_model}_whole-repo.stream.jsonl"

    shared_workdir = getattr(submit_session, "workdir", None)
    workspace = (nullcontext(str(shared_workdir)) if shared_workdir is not None
                 else tempfile.TemporaryDirectory(prefix="codex_whole_repo_"))
    with workspace as workdir:
        lines, _returncode, run_error = run_process(
            command, cwd=workdir, env=_child_env(ctx, api_key),
            timeout=session_timeout, stream_log=stream_log, label="codex")

    if stream_log is not None:
        with stream_log.open(encoding="utf-8") as stream:
            parsed = parse_stream(stream)
    else:
        parsed = parse_stream(lines)
    trajectory, usage = parsed["trajectory"], parsed["usage"]
    run_error = run_error or _event_error(parsed["result_event"])
    if trajectory_dir is not None:
        save_trajectory(trajectory, trajectory_dir / f"{safe_model}_whole-repo.jsonl")
    rows = submit_session.result_rows() if submit_session is not None else []
    return {"rows": rows, "tokens_k": round(usage.total_tokens / 1000, 2),
            "trajectory": trajectory, "usage": usage, "error": run_error}
