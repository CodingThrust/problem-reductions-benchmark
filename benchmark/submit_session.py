"""Evaluation-owned counterexample submission budget.

The agent-facing ``submit`` process is deliberately only a thin client. Requests use an
atomic file spool inside the agent scratch workspace so sandboxed Codex, Claude, mini-swe,
and container backends share one transport. The budget, verification, response cache, and
accepted-certificate ledger remain authoritative in runner memory; editing spool files
cannot reset the budget or forge a scored result.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import shlex
import stat
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from benchmark.verify import Verdict, verify

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_CACHED_RESPONSES = 1024


class SubmissionSession:
    """Own one run-wide submit budget and its append-only in-memory ledger."""

    def __init__(self, limit: int = 100, verifier: Callable[[dict], Verdict] = verify):
        if limit < 0:
            raise ValueError("submit limit must be >= 0")
        self.limit = limit
        self._verifier = verifier
        self._attempts: list[dict] = []
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._tmpdir: Path | None = None
        self._workdir: Path | None = None
        self._channel_dir: Path | None = None
        self._inbox_dir: Path | None = None
        self._processing_dir: Path | None = None
        self._outbox_dir: Path | None = None
        self._workdir_fd: int | None = None
        self._inbox_fd: int | None = None
        self._processing_fd: int | None = None
        self._outbox_fd: int | None = None
        self._artifact_fd: int | None = None
        self._artifact_dir: Path | None = None
        self._old_channel_env: str | None = None
        self._old_artifact_env: str | None = None
        self._old_path: str | None = None
        self._stopping = threading.Event()
        self._responses: dict[str, dict] = {}
        self._status_checks = 0

    @property
    def used(self) -> int:
        with self._lock:
            return len(self._attempts)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def attempts(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._attempts)

    @property
    def workdir(self) -> Path:
        """Writable scratch root shared with sandboxed headless agents."""
        if self._workdir is None:
            raise RuntimeError("submission session is not active")
        return self._workdir

    @property
    def status_checks(self) -> int:
        with self._lock:
            return self._status_checks

    @property
    def reachable(self) -> bool:
        """Whether any status or submit request reached the authoritative service."""
        with self._lock:
            return self._status_checks > 0 or bool(self._attempts)

    def __enter__(self) -> "SubmissionSession":
        self._tmpdir = Path(tempfile.mkdtemp(prefix="prb-agent-", dir="/tmp"))
        self._workdir = self._tmpdir / "work"
        bin_dir = self._workdir / ".prb-bin"
        self._channel_dir = self._workdir / ".prb-submit"
        self._inbox_dir = self._channel_dir / "inbox"
        self._processing_dir = self._channel_dir / "processing"
        self._outbox_dir = self._channel_dir / "outbox"
        self._artifact_dir = self._workdir / "artifacts"
        for directory in (bin_dir, self._inbox_dir, self._processing_dir,
                          self._outbox_dir, self._artifact_dir):
            directory.mkdir(parents=True, exist_ok=True)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        self._workdir_fd = os.open(self._workdir, directory_flags)
        self._inbox_fd = os.open(self._inbox_dir, directory_flags)
        self._processing_fd = os.open(self._processing_dir, directory_flags)
        self._outbox_fd = os.open(self._outbox_dir, directory_flags)
        self._artifact_fd = os.open(self._artifact_dir, directory_flags)

        shim = bin_dir / "submit"
        client = Path(__file__).with_name("agent_submit.py")
        shim.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(client))} \"$@\"\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)

        self._thread = threading.Thread(target=self._serve, name="prb-submit", daemon=True)
        self._thread.start()

        self._old_channel_env = os.environ.get("PRB_SUBMIT_DIR")
        self._old_artifact_env = os.environ.get("PRB_ARTIFACT_DIR")
        self._old_path = os.environ.get("PATH")
        os.environ["PRB_SUBMIT_DIR"] = str(self._channel_dir)
        os.environ["PRB_ARTIFACT_DIR"] = str(self._artifact_dir)
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{self._old_path or ''}"
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._old_channel_env is None:
            os.environ.pop("PRB_SUBMIT_DIR", None)
        else:
            os.environ["PRB_SUBMIT_DIR"] = self._old_channel_env
        if self._old_artifact_env is None:
            os.environ.pop("PRB_ARTIFACT_DIR", None)
        else:
            os.environ["PRB_ARTIFACT_DIR"] = self._old_artifact_env
        if self._old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self._old_path

        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        for fd in (self._workdir_fd, self._inbox_fd, self._processing_fd,
                   self._outbox_fd, self._artifact_fd):
            if fd is not None:
                os.close(fd)
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def attempts_since(self, index: int) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._attempts[index:])

    def preserve_artifacts(self, destination: str | Path) -> list[Path]:
        """Copy small certificate artifacts out of the disposable agent workspace."""
        destination = Path(destination)
        copied: list[Path] = []
        sources = ((self._artifact_fd, "artifacts", False),
                   (self._workdir_fd, "workspace", True))
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        for directory_fd, label, certificate_names_only in sources:
            if directory_fd is None:
                continue
            for name in os.listdir(directory_fd):
                if certificate_names_only and ("cert" not in name.lower()
                                               or not name.lower().endswith(".json")):
                    continue
                try:
                    source_fd = os.open(name, flags, dir_fd=directory_fd)
                except OSError:
                    continue
                try:
                    info = os.fstat(source_fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REQUEST_BYTES:
                        continue
                    with os.fdopen(source_fd, "rb", closefd=False) as source_file:
                        payload = source_file.read(MAX_REQUEST_BYTES + 1)
                finally:
                    os.close(source_fd)
                if len(payload) > MAX_REQUEST_BYTES:
                    continue
                target = destination / label / Path(name).name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                copied.append(target)
        return copied

    def result_rows(self) -> list[dict]:
        """Collapse attempts to one authoritative result per named rule.

        An accepted attempt wins over rejected attempts for the same rule.  Parse failures
        remain in ``submit_log`` for auditing but cannot form schema-valid result rows.
        """
        by_rule: dict[str, dict] = {}
        for attempt in self.attempts:
            rule = attempt.get("rule")
            cert = attempt.get("certificate")
            if (not isinstance(rule, str) or not isinstance(cert, dict)
                    or not isinstance(cert.get("rule"), str)
                    or not isinstance(cert.get("source"), dict)):
                continue
            old = by_rule.get(rule)
            if old is None or (attempt.get("accepted") and not old.get("accepted")):
                by_rule[rule] = attempt
        return [_attempt_to_row(a) for a in by_rule.values()]

    def _serve(self) -> None:
        assert self._inbox_fd is not None
        assert self._processing_fd is not None
        while not self._stopping.is_set():
            handled = False
            for name in os.listdir(self._inbox_fd):
                if not (len(name) == 37 and name.endswith(".json")
                        and all(char in "0123456789abcdef" for char in name[:-5])):
                    continue
                handled = True
                try:
                    os.replace(name, name, src_dir_fd=self._inbox_fd,
                               dst_dir_fd=self._processing_fd)
                except FileNotFoundError:
                    continue
                request_id = name[:-5]
                response = self._responses.get(request_id)
                if response is None:
                    try:
                        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                        request_fd = os.open(name, flags, dir_fd=self._processing_fd)
                        try:
                            info = os.fstat(request_fd)
                            if not stat.S_ISREG(info.st_mode):
                                raise ValueError("request is not a regular file")
                            if info.st_size > MAX_REQUEST_BYTES:
                                raise ValueError(f"request exceeds {MAX_REQUEST_BYTES} bytes")
                            with os.fdopen(request_fd, "rb", closefd=False) as request_file:
                                raw = request_file.read(MAX_REQUEST_BYTES + 1)
                        finally:
                            os.close(request_fd)
                        if len(raw) > MAX_REQUEST_BYTES:
                            raise ValueError(f"request exceeds {MAX_REQUEST_BYTES} bytes")
                        request = json.loads(raw.decode("utf-8"))
                        if request.get("request_id") != request_id:
                            raise ValueError("request id does not match filename")
                        response = self._handle(request)
                    except Exception as e:  # malformed requests do not kill the service
                        response = self._record_rejection(f"invalid request: {e}")
                    self._responses[request_id] = response
                    if len(self._responses) > MAX_CACHED_RESPONSES:
                        self._responses.pop(next(iter(self._responses)))
                self._write_response(request_id, response)
                try:
                    os.unlink(name, dir_fd=self._processing_fd)
                except FileNotFoundError:
                    pass
            if not handled:
                self._stopping.wait(0.02)

    def _write_response(self, request_id: str, response: dict) -> None:
        assert self._outbox_fd is not None
        destination = f"{request_id}.json"
        temporary = f".{request_id}.{threading.get_ident()}.tmp"
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0))
        response_fd = os.open(temporary, flags, 0o600, dir_fd=self._outbox_fd)
        try:
            with os.fdopen(response_fd, "w", encoding="utf-8", closefd=False) as response_file:
                json.dump(response, response_file)
                response_file.flush()
        finally:
            os.close(response_fd)
        os.replace(temporary, destination, src_dir_fd=self._outbox_fd,
                   dst_dir_fd=self._outbox_fd)

    def _handle(self, request: dict) -> dict:
        if request.get("op") == "status":
            with self._lock:
                self._status_checks += 1
            return {"status": "ok", "used": self.used, "limit": self.limit,
                    "remaining": self.remaining}
        if request.get("op") != "submit":
            return {"accepted": False, "error": "unknown operation", "used": self.used,
                    "limit": self.limit, "remaining": self.remaining}

        # Verification is serialized under this lock.  The budget check + append is atomic,
        # including if a future runner enables parallel agent sessions.
        with self._lock:
            if len(self._attempts) >= self.limit:
                return {"accepted": False, "exhausted": True,
                        "reason": f"submit budget exhausted ({self.limit}/{self.limit})",
                        "used": len(self._attempts), "limit": self.limit, "remaining": 0}

            text = request.get("certificate_text")
            cert = None
            accepted = False
            details = None
            if isinstance(request.get("client_error"), str):
                reason = request["client_error"]
            elif not isinstance(text, str):
                reason = "certificate payload must be text"
            else:
                try:
                    cert = json.loads(text)
                except json.JSONDecodeError as e:
                    reason = f"invalid certificate JSON: {e.msg} (line {e.lineno}, column {e.colno})"
                else:
                    if not isinstance(cert, dict):
                        reason = "certificate must be a JSON object"
                    else:
                        try:
                            verdict = self._verifier(cert)
                            accepted = bool(verdict.accepted)
                            reason = verdict.reason
                            details = verdict.details
                        except Exception as e:  # one bad certificate must not kill the budget server
                            reason = f"verifier error: {type(e).__name__}: {e}"

            return self._append_attempt(cert, accepted, reason, details)

    def _record_rejection(self, reason: str) -> dict:
        with self._lock:
            if len(self._attempts) >= self.limit:
                return {"accepted": False, "exhausted": True, "reason": reason,
                        "used": self.limit, "limit": self.limit, "remaining": 0}
            return self._append_attempt(None, False, reason)

    def _append_attempt(self, cert, accepted: bool, reason: str, details=None) -> dict:
        attempt_no = len(self._attempts) + 1
        record = {
            "attempt": attempt_no,
            "accepted": accepted,
            "rule": cert.get("rule") if isinstance(cert, dict) else None,
            "reason": reason,
            "certificate": cert,
        }
        if details is not None:
            record["verify_details"] = details
        self._attempts.append(record)
        return {**record, "used": attempt_no, "limit": self.limit,
                "remaining": self.limit - attempt_no}


def _attempt_to_row(attempt: dict) -> dict:
    row = {
        "rule": attempt["rule"],
        "result": "bug_found" if attempt.get("accepted") else "rejected",
        "tokens_k": 0.0,
        "certificate": copy.deepcopy(attempt["certificate"]),
        "submit_attempt": attempt["attempt"],
    }
    if attempt.get("accepted"):
        if attempt.get("verify_details") is not None:
            row["verify_details"] = copy.deepcopy(attempt["verify_details"])
    else:
        row["reject_reason"] = attempt.get("reason", "rejected")
    return row
