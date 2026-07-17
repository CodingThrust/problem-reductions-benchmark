"""Shared process and prompt helpers for local headless-agent backends."""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
from collections import deque
from contextlib import nullcontext
from pathlib import Path
from typing import Callable

from benchmark.usage import Usage


def safe_model_label(model: str) -> str:
    """Return a model name that is safe to use in a log filename."""
    return model.replace("/", "_").replace(":", "_")


def render_prompt(template: str, variables: dict) -> str:
    import jinja2  # dependency of the runner stack

    return jinja2.Template(template).render(**variables)


def load_rendered_prompts(config_path, default_config: Path, strategy: str | None,
                          variables: dict) -> tuple[str, str]:
    """Load the shared agent config and render its system/task prompt pair."""
    from benchmark.run_mini import _load_agent_config

    agent_cfg, _model_cfg, strategy = _load_agent_config(
        config_path, default_config, strategy)
    variables = {**variables, "strategy": strategy}
    return (
        render_prompt(agent_cfg.get("system_template", ""), variables),
        render_prompt(agent_cfg.get("instance_template", ""), variables),
    )


def child_env(ctx, api_key: str | None = None, *, api_key_var: str | None = None) -> dict:
    """Build a CLI child environment with ``pred`` and an optional generic key."""
    env = dict(os.environ)
    env["PATH"] = f"{Path(ctx.pred_binary).parent}{os.pathsep}{env.get('PATH', '')}"
    if api_key and api_key_var and not env.get(api_key_var):
        env[api_key_var] = api_key
    return env


def run_process(cmd: list[str], *, cwd: str, env: dict, timeout: int,
                stream_log: Path | None = None,
                label: str | None = None) -> tuple[list[str], int, str | None]:
    """Run a CLI with a hard timeout and persist its stdout event stream when requested.

    When ``stream_log`` is set, stdout is not also retained in memory. Callers parse the
    file afterward as an iterable. Stderr is drained concurrently into a sibling log so it
    cannot deadlock the child or corrupt a JSONL event stream.
    """
    label = label or Path(cmd[0]).name
    log_handle = open(stream_log, "w", encoding="utf-8") if stream_log is not None else None
    stderr_path = (stream_log.with_name(f"{stream_log.name}.stderr.log")
                   if stream_log is not None else None)
    stderr_handle = (open(stderr_path, "w", encoding="utf-8")
                     if stderr_path is not None else None)
    lines: list[str] = []
    error_tail: deque[str] = deque(maxlen=20)
    timed_out = threading.Event()
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, text=True, start_new_session=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                error_tail.append(line[-500:])
                if stderr_handle is not None:
                    stderr_handle.write(line)

        stderr_thread = threading.Thread(target=_drain_stderr, name=f"{label}-stderr",
                                         daemon=True)
        stderr_thread.start()

        def _kill() -> None:
            timed_out.set()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        watchdog = threading.Timer(timeout, _kill)
        watchdog.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if log_handle is None:
                    lines.append(line)
                error_tail.append(line[-500:])
                if log_handle is not None:
                    log_handle.write(line)
            proc.wait()
            stderr_thread.join()
        finally:
            watchdog.cancel()
    except FileNotFoundError:
        return lines, -1, f"{label} CLI not found: {cmd[0]!r}"
    except OSError as e:
        return lines, -1, str(e)
    finally:
        if log_handle is not None:
            log_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()

    if timed_out.is_set():
        return lines, proc.returncode, f"{label} session exceeded {timeout}s"
    if proc.returncode != 0:
        detail = "".join(error_tail).strip()[-500:]
        return lines, proc.returncode, f"{label} exited {proc.returncode}: {detail}"
    return lines, proc.returncode, None


def run_headless_session(
    *,
    command: list[str],
    model_name: str,
    env: dict,
    timeout: int,
    parser: Callable[..., dict],
    label: str,
    trajectory_dir: Path | None,
    submit_session=None,
    event_error: Callable[[dict | None], str | None] | None = None,
) -> dict:
    """Run a headless CLI once and retain its raw stream as the single session log."""
    stream_log = None
    if trajectory_dir is not None:
        trajectory_dir = Path(trajectory_dir).resolve()
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        stream_log = trajectory_dir / (
            f"{safe_model_label(model_name)}_whole-repo.stream.jsonl")

    shared_workdir = getattr(submit_session, "workdir", None)
    workspace = (nullcontext(str(shared_workdir)) if shared_workdir is not None
                 else tempfile.TemporaryDirectory(prefix=f"{label}_whole_repo_"))
    with workspace as workdir:
        lines, _returncode, run_error = run_process(
            command, cwd=workdir, env=env, timeout=timeout,
            stream_log=stream_log, label=label)

    if stream_log is None:
        parsed = parser(lines, collect_trajectory=False)
    else:
        with stream_log.open(encoding="utf-8") as stream:
            parsed = parser(stream, collect_trajectory=False)
    usage = parsed.get("usage") or Usage()
    if event_error is not None:
        run_error = run_error or event_error(parsed.get("result_event"))
    return {
        "tokens_k": round(usage.total_tokens / 1000, 2),
        "usage": usage,
        "error": run_error,
    }
