"""Run mini-swe shell actions as the dedicated unprivileged agent identity."""
from __future__ import annotations

import os
import subprocess

from benchmark.observation_policy import ObservationStore
from benchmark.process_control import ProcessLimits, run_capped_process

_SAFE_ENV_KEYS = {"PATH", "PYTHONPATH", "LANG", "LC_ALL", "PAGER", "MANPAGER", "LESS",
                  "PRB_PRED_DIR", "PRB_SUBMIT_DIR", "PRB_ARTIFACT_DIR",
                  "PRB_PRED_TIMEOUT", "PRB_SUBMIT_TIMEOUT"}


def sanitized_agent_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return the small non-secret environment visible to model-authored commands."""
    merged = dict(os.environ)
    merged.update(extra or {})
    return {key: value for key, value in merged.items() if key in _SAFE_ENV_KEYS}


def run_as_agent(
    command: str,
    *,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    uid: int,
    gid: int,
    extra_groups: tuple[int, ...] = (),
    max_output_chars: int = 10_000,
    observation_store: ObservationStore | None = None,
    observation_kind: str = "shell",
) -> subprocess.CompletedProcess[str]:
    """Execute one shell action after irreversibly dropping child privileges."""
    if os.name != "posix":
        raise RuntimeError("the rankable agent environment requires POSIX privilege separation")
    if os.geteuid() != 0 and (uid != os.getuid() or gid != os.getgid()):
        raise PermissionError("runner must be root to launch a different agent identity")

    result = run_capped_process(
        command,
        shell=True,
        cwd=cwd,
        env=env,
        timeout=timeout,
        max_output_chars=(observation_store.config.archive_chars
                          if observation_store is not None else max_output_chars),
        combine_stderr=True,
        uid=uid,
        gid=gid,
        extra_groups=extra_groups,
        limits=ProcessLimits(cpu_seconds=max(1, timeout), memory_bytes=2 * 1024 ** 3,
                             file_bytes=64 * 1024 ** 2),
    )
    packaged = None
    output = result.stdout
    if observation_store is not None:
        packaged = observation_store.package(
            kind=observation_kind, command=command, returncode=result.returncode,
            timed_out=result.timed_out, stdout=result.stdout, stderr=result.stderr,
            original_chars=result.original_chars, original_lines=result.original_lines,
            archive_truncated=result.capture_truncated)
        output = packaged.preview
    if result.timed_out:
        error = subprocess.TimeoutExpired(command, timeout, output=output)
        error.observation_metadata = packaged.metadata if packaged is not None else None
        raise error
    completed = subprocess.CompletedProcess(command, result.returncode, stdout=output)
    completed.observation_metadata = packaged.metadata if packaged is not None else None
    return completed


def package_shell_result(session, command: str, *, output: str, returncode: int,
                         exception_info: str = "", timed_out: bool = False) -> dict:
    """Package a harness-generated shell result through the same frozen policy."""
    packaged = session.observations.package(
        kind="shell", command=command, returncode=returncode, timed_out=timed_out,
        stdout=output, stderr="", original_chars=len(output),
        original_lines=output.count("\n") + int(bool(output) and not output.endswith("\n")),
        archive_truncated=False)
    session.record_shell_observation(command, packaged.metadata)
    return {"output": packaged.preview, "returncode": returncode,
            "exception_info": exception_info}


def make_agent_environment(session, *, uid: int, gid: int,
                           extra_groups: tuple[int, ...] = (), timeout: int = 300):
    """Construct a pinned mini-swe LocalEnvironment with a privilege-dropping execute path."""
    from minisweagent.environments.local import LocalEnvironment

    class EvidenceLocalEnvironment(LocalEnvironment):
        def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
            command = action.get("command", "")
            action_cwd = cwd or self.config.cwd or str(session.workdir)
            if not session.admit_shell_action(command):
                output = package_shell_result(
                    session, command, output="shell action budget exhausted\n", returncode=75)
                self._check_finished(output)
                return output
            try:
                result = run_as_agent(
                    command,
                    cwd=action_cwd,
                    env=sanitized_agent_env(self.config.env),
                    timeout=timeout or self.config.timeout,
                    uid=uid,
                    gid=gid,
                    extra_groups=extra_groups,
                    max_output_chars=session.budget.max_output_chars,
                    observation_store=session.observations,
                )
                output = {"output": result.stdout, "returncode": result.returncode,
                          "exception_info": ""}
                session.record_shell_observation(command, result.observation_metadata)
            except Exception as error:
                raw_output = getattr(error, "output", None)
                raw_output = (raw_output.decode("utf-8", errors="replace")
                              if isinstance(raw_output, bytes) else (raw_output or ""))
                metadata = getattr(error, "observation_metadata", None)
                if metadata is not None:
                    session.record_shell_observation(command, metadata)
                    output = {
                        "output": raw_output, "returncode": -1,
                        "exception_info": f"An error occurred while executing the command: {error}",
                    }
                else:
                    output = package_shell_result(
                        session, command, output=raw_output, returncode=-1,
                        exception_info=f"An error occurred while executing the command: {error}")
                output["extra"] = {"exception_type": type(error).__name__,
                                   "exception": str(error)}
            self._check_finished(output)
            return output

    return EvidenceLocalEnvironment(cwd=str(session.workdir), timeout=timeout)
