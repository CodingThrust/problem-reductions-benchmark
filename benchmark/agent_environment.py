"""Run mini-swe shell actions as the dedicated unprivileged agent identity."""
from __future__ import annotations

import os
import subprocess

from benchmark.process_control import ProcessLimits, run_capped_process


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
        max_output_chars=max_output_chars,
        combine_stderr=True,
        uid=uid,
        gid=gid,
        extra_groups=extra_groups,
        limits=ProcessLimits(cpu_seconds=max(1, timeout), memory_bytes=2 * 1024 ** 3,
                             file_bytes=64 * 1024 ** 2),
    )
    if result.timed_out:
        raise subprocess.TimeoutExpired(command, timeout, output=result.stdout)
    return subprocess.CompletedProcess(command, result.returncode, stdout=result.stdout)


def make_agent_environment(session, *, uid: int, gid: int,
                           extra_groups: tuple[int, ...] = (), timeout: int = 300):
    """Construct a pinned mini-swe LocalEnvironment with a privilege-dropping execute path."""
    from minisweagent.environments.local import LocalEnvironment

    class EvidenceLocalEnvironment(LocalEnvironment):
        def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
            command = action.get("command", "")
            action_cwd = cwd or self.config.cwd or str(session.workdir)
            if not session.admit_shell_action(command):
                output = {
                    "output": "shell action budget exhausted\n",
                    "returncode": 75,
                    "exception_info": "",
                }
                self._check_finished(output)
                return output
            try:
                result = run_as_agent(
                    command,
                    cwd=action_cwd,
                    env=os.environ | self.config.env,
                    timeout=timeout or self.config.timeout,
                    uid=uid,
                    gid=gid,
                    extra_groups=extra_groups,
                    max_output_chars=session.budget.max_output_chars,
                )
                output = {"output": result.stdout, "returncode": result.returncode,
                          "exception_info": ""}
            except Exception as error:
                raw_output = getattr(error, "output", None)
                raw_output = (raw_output.decode("utf-8", errors="replace")
                              if isinstance(raw_output, bytes) else (raw_output or ""))
                output = {
                    "output": raw_output,
                    "returncode": -1,
                    "exception_info": f"An error occurred while executing the command: {error}",
                    "extra": {"exception_type": type(error).__name__,
                              "exception": str(error)},
                }
            self._check_finished(output)
            return output

    return EvidenceLocalEnvironment(cwd=str(session.workdir), timeout=timeout)
