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
    original_chars: int
    original_lines: int
    capture_truncated: bool


class _StreamCapture:
    def __init__(self, limit: int):
        retained_limit = max(0, limit - 160)
        self.head_limit = retained_limit // 2
        self.tail_limit = retained_limit - self.head_limit
        self.total = 0
        self.newlines = 0
        self.ends_newline = True
        self.head: list[str] = []
        self.head_chars = 0
        self.tail: list[str] = []
        self.tail_chars = 0

    def add(self, chunk: str) -> None:
        self.total += len(chunk)
        self.newlines += chunk.count("\n")
        self.ends_newline = chunk.endswith("\n")
        head_remaining = self.head_limit - self.head_chars
        if head_remaining:
            piece = chunk[:head_remaining]
            self.head.append(piece)
            self.head_chars += len(piece)
            chunk = chunk[len(piece):]
        if chunk and self.tail_limit:
            self.tail.append(chunk)
            self.tail_chars += len(chunk)
            while self.tail_chars > self.tail_limit:
                excess = self.tail_chars - self.tail_limit
                if len(self.tail[0]) <= excess:
                    self.tail_chars -= len(self.tail.pop(0))
                else:
                    self.tail[0] = self.tail[0][excess:]
                    self.tail_chars -= excess

    def result(self) -> tuple[str, int, bool]:
        retained = "".join(self.head + self.tail)
        truncated = self.total > len(retained)
        if truncated:
            retained += f"\n... bounded archive: {self.total - len(retained)} characters omitted ...\n"
        lines = self.newlines + int(self.total > 0 and not self.ends_newline)
        return retained, lines, truncated


class _CombinedCapture:
    def __init__(self, limit: int, *, split_streams: bool):
        stdout_limit = limit // 2 if split_streams else limit
        stderr_limit = limit - stdout_limit if split_streams else 0
        self.streams = {
            "stdout": _StreamCapture(stdout_limit),
            "stderr": _StreamCapture(stderr_limit),
        }

    @property
    def total(self) -> int:
        return sum(stream.total for stream in self.streams.values())

    def drain(self, stream, key: str) -> None:
        capture = self.streams[key]
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            capture.add(chunk)

    def result(self) -> tuple[str, str, int, bool]:
        stdout, stdout_lines, stdout_truncated = self.streams["stdout"].result()
        stderr, stderr_lines, stderr_truncated = self.streams["stderr"].result()
        return (stdout, stderr, stdout_lines + stderr_lines,
                stdout_truncated or stderr_truncated)


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
        capture = _CombinedCapture(max_output_chars, split_streams=not combine_stderr)
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
        stdout, stderr, original_lines, capture_truncated = capture.result()
        return ProcessResult(
            returncode=124 if timed_out else process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            original_chars=capture.total,
            original_lines=original_lines,
            capture_truncated=capture_truncated,
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
