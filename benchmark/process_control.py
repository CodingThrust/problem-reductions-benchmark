"""Bounded subprocess execution shared by agent actions and pred gateway calls."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Sequence

try:  # POSIX-only; official rankable runs use Linux.
    import resource
except ImportError:  # pragma: no cover - Windows is not a rankable platform
    resource = None


@dataclass(frozen=True)
class ProcessLimits:
    cpu_seconds: int
    memory_bytes: int
    file_bytes: int
    processes: int = 64


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


class _CombinedCapture:
    def __init__(self, limit: int):
        self.limit = limit
        self.remaining = limit
        self.total = 0
        self.totals = {"stdout": 0, "stderr": 0}
        self.parts = {"stdout": [], "stderr": []}
        self._lock = threading.Lock()

    def drain(self, stream, key: str) -> None:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            with self._lock:
                self.total += len(chunk)
                self.totals[key] += len(chunk)
                if self.remaining:
                    piece = chunk[:self.remaining]
                    self.parts[key].append(piece)
                    self.remaining -= len(piece)

    def result(self) -> tuple[str, str]:
        stdout = "".join(self.parts["stdout"])
        stderr = "".join(self.parts["stderr"])
        retained = len(stdout) + len(stderr)
        if self.total > retained:
            marker = f"\n... {self.total - retained} characters elided ...\n"
            keep = max(0, self.limit - len(marker))
            if self.totals["stdout"] > len(stdout):
                stdout = stdout[:keep] + marker[:self.limit - min(len(stdout), keep)]
                stderr = ""
            else:
                stdout = stdout[:keep]
                stderr_keep = max(0, keep - len(stdout))
                stderr = stderr[:stderr_keep] + marker[:self.limit - len(stdout) - stderr_keep]
        return stdout, stderr


def run_capped_process(
    command: str | Sequence[str],
    *,
    shell: bool,
    cwd: str,
    env: dict[str, str] | None,
    timeout: int,
    max_output_chars: int,
    combine_stderr: bool = False,
    uid: int | None = None,
    gid: int | None = None,
    extra_groups: Sequence[int] = (),
    limits: ProcessLimits | None = None,
    on_start: Callable[[subprocess.Popen], None] | None = None,
    on_finish: Callable[[subprocess.Popen], None] | None = None,
) -> ProcessResult:
    """Run one process with a combined observation cap and kill its whole group on timeout."""
    privilege_args = {}
    if uid is not None or gid is not None:
        if uid is None or gid is None:
            raise ValueError("uid and gid must be provided together")
        if os.name != "posix":
            raise RuntimeError("rankable privilege separation requires POSIX")
        if os.geteuid() == 0:
            privilege_args = {"user": uid, "group": gid, "extra_groups": list(extra_groups)}
        elif uid != os.getuid() or gid != os.getgid():
            raise PermissionError("runner must be root to launch a different identity")

    process = subprocess.Popen(
        command,
        shell=shell,
        text=True,
        cwd=cwd,
        env=env,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if combine_stderr else subprocess.PIPE,
        start_new_session=True,
        **privilege_args,
    )
    if on_start is not None:
        on_start(process)
    try:
        _apply_limits(process.pid, limits)
        capture = _CombinedCapture(max_output_chars)
        assert process.stdout is not None
        readers = [threading.Thread(target=capture.drain, args=(process.stdout, "stdout"),
                                    daemon=True)]
        if not combine_stderr:
            assert process.stderr is not None
            readers.append(threading.Thread(target=capture.drain,
                                            args=(process.stderr, "stderr"), daemon=True))
        for reader in readers:
            reader.start()

        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_process_group(process)
            process.wait()
        else:
            # A successful shell leader may leave background children behind. Kill the
            # entire private process group before reading pipes to completion so no process
            # can survive into a later rule episode or keep a pipe open indefinitely.
            terminate_process_group(process)
        for reader in readers:
            reader.join()
        stdout, stderr = capture.result()
        return ProcessResult(
            returncode=124 if timed_out else process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )
    finally:
        if on_finish is not None:
            on_finish(process)


def terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _apply_limits(pid: int, limits: ProcessLimits | None) -> None:
    if limits is None or resource is None or not hasattr(resource, "prlimit"):
        return
    for name, value in (
        ("RLIMIT_CPU", limits.cpu_seconds),
        ("RLIMIT_AS", limits.memory_bytes),
        ("RLIMIT_FSIZE", limits.file_bytes),
        ("RLIMIT_NPROC", limits.processes),
    ):
        resource_id = getattr(resource, name, None)
        if resource_id is None:
            continue
        try:
            resource.prlimit(pid, resource_id, (value, value))
        except (PermissionError, ProcessLookupError, ValueError, OSError):
            # macOS lacks prlimit and some kernels reject individual limits. Official
            # Linux runs additionally carry fixed container-level resource limits.
            continue
