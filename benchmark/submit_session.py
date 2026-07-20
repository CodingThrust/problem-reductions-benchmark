"""Evaluation-owned counterexample submission budget.

The agent-facing ``submit`` process is deliberately only a thin client. Requests use an
atomic file spool inside the agent scratch workspace. The budget, verification, and
accepted-certificate ledger remain authoritative in runner memory; editing spool files
cannot reset the budget or forge a scored result.
"""
from __future__ import annotations

import copy
import hashlib
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


class SubmissionSession:
    """Own one rule episode's submit budget and append-only in-memory ledger."""

    def __init__(self, expected_rule: str, limit: int = 2,
                 verifier: Callable[[dict], Verdict] = verify):
        if limit < 0:
            raise ValueError("submit limit must be >= 0")
        if not expected_rule:
            raise ValueError("expected_rule must be non-empty")
        self.limit = limit
        self._verifier = verifier
        self.expected_rule = expected_rule
        self._attempts: list[dict] = []
        self._responses: dict[str, tuple[str, dict]] = {}
        self._closed = False
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._tmpdir: Path | None = None
        self._workdir: Path | None = None
        self._inbox_fd: int | None = None
        self._processing_fd: int | None = None
        self._outbox_fd: int | None = None
        self._old_channel_env: str | None = None
        self._old_artifact_env: str | None = None
        self._old_path: str | None = None
        self._stopping = threading.Event()

    @property
    def used(self) -> int:
        with self._lock:
            return len(self._attempts)

    @property
    def remaining(self) -> int:
        if self.closed:
            return 0
        return max(0, self.limit - self.used)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def attempts(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._attempts)

    @property
    def workdir(self) -> Path:
        """Writable scratch root shared with the sandboxed benchmark agent."""
        if self._workdir is None:
            raise RuntimeError("submission session is not active")
        return self._workdir

    def __enter__(self) -> "SubmissionSession":
        self._tmpdir = Path(tempfile.mkdtemp(prefix="prb-agent-", dir="/tmp"))
        self._workdir = self._tmpdir / "work"
        bin_dir = self._workdir / ".prb-bin"
        channel_dir = self._workdir / ".prb-submit"
        inbox_dir = channel_dir / "inbox"
        processing_dir = channel_dir / "processing"
        outbox_dir = channel_dir / "outbox"
        artifact_dir = self._workdir / "artifacts"
        for directory in (bin_dir, inbox_dir, processing_dir, outbox_dir, artifact_dir):
            directory.mkdir(parents=True, exist_ok=True)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        self._inbox_fd = os.open(inbox_dir, directory_flags)
        self._processing_fd = os.open(processing_dir, directory_flags)
        self._outbox_fd = os.open(outbox_dir, directory_flags)

        shim = bin_dir / "submit"
        package_root = Path(__file__).resolve().parent.parent
        shim.write_text(
            f"#!/bin/sh\nPYTHONPATH={shlex.quote(str(package_root))}${{PYTHONPATH:+:$PYTHONPATH}} "
            f"exec {shlex.quote(sys.executable)} -m benchmark.agent_submit \"$@\"\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)

        self._thread = threading.Thread(target=self._serve, name="prb-submit", daemon=True)
        self._thread.start()

        self._old_channel_env = os.environ.get("PRB_SUBMIT_DIR")
        self._old_artifact_env = os.environ.get("PRB_ARTIFACT_DIR")
        self._old_path = os.environ.get("PATH")
        os.environ["PRB_SUBMIT_DIR"] = str(channel_dir)
        os.environ["PRB_ARTIFACT_DIR"] = str(artifact_dir)
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
            self._thread.join()
        for fd in (self._inbox_fd, self._processing_fd, self._outbox_fd):
            if fd is not None:
                os.close(fd)
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def prepare_agent_access(self, uid: int, gid: int) -> None:
        """Give a non-root agent access to its request queues and artifact directory."""
        if self._tmpdir is None:
            raise RuntimeError("submission session is not active")
        self._tmpdir.chmod(0o711)
        (self._tmpdir / "work").chmod(0o711)
        paths = (
            self._tmpdir / "work" / ".prb-submit" / "inbox",
            self._tmpdir / "work" / ".prb-submit" / "outbox",
            self._tmpdir / "work" / "artifacts",
        )
        for path in paths:
            os.chown(path, uid, gid)
            path.chmod(0o700)

    def _serve(self) -> None:
        assert self._inbox_fd is not None
        assert self._processing_fd is not None
        idle_wait = 0.02
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
                raw = b""
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
                    response = self._handle_idempotent(request_id, request)
                except Exception as e:  # malformed requests do not kill the service
                    response = self._handle_malformed_idempotent(request_id, raw, e)
                self._write_response(request_id, response)
                try:
                    os.unlink(name, dir_fd=self._processing_fd)
                except FileNotFoundError:
                    pass
            if handled:
                idle_wait = 0.02
            else:
                self._stopping.wait(idle_wait)
                idle_wait = min(idle_wait * 2, 0.5)

    def _write_response(self, request_id: str, response: dict) -> None:
        assert self._outbox_fd is not None
        destination = f"{request_id}.json"
        temporary = f".{request_id}.{threading.get_ident()}.tmp"
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0))
        # The service may run as root while the agent client is deliberately unprivileged.
        response_fd = os.open(temporary, flags, 0o644, dir_fd=self._outbox_fd)
        try:
            with os.fdopen(response_fd, "w", encoding="utf-8", closefd=False) as response_file:
                json.dump(response, response_file)
                response_file.flush()
        finally:
            os.close(response_fd)
        os.replace(temporary, destination, src_dir_fd=self._outbox_fd,
                   dst_dir_fd=self._outbox_fd)

    def _handle_idempotent(self, request_id: str, request: dict) -> dict:
        fingerprint = json.dumps(request, sort_keys=True, separators=(",", ":"))
        with self._lock:
            old = self._responses.get(request_id)
            if old is not None:
                old_fingerprint, old_response = old
                if old_fingerprint != fingerprint:
                    return {"accepted": False, "infrastructure_error": True,
                            "reason": "request id was reused with a different payload",
                            "used": self.used, "limit": self.limit,
                            "remaining": self.remaining}
                return copy.deepcopy(old_response)
        response = self._handle(request, request_id=request_id)
        with self._lock:
            self._responses[request_id] = (fingerprint, copy.deepcopy(response))
        return response

    def _handle_malformed_idempotent(
        self, request_id: str, raw: bytes, error: Exception
    ) -> dict:
        fingerprint = f"invalid:{hashlib.sha256(raw).hexdigest()}"
        with self._lock:
            old = self._responses.get(request_id)
            if old is not None:
                old_fingerprint, old_response = old
                if old_fingerprint != fingerprint:
                    return {"accepted": False, "infrastructure_error": True,
                            "reason": "request id was reused with a different payload",
                            "used": self.used, "limit": self.limit,
                            "remaining": self.remaining}
                return copy.deepcopy(old_response)
            response = self._record_rejection(
                f"invalid request: {type(error).__name__}: {error}", request_id=request_id)
            self._responses[request_id] = (fingerprint, copy.deepcopy(response))
            return response

    def _handle(self, request: dict, *, request_id: str | None = None) -> dict:
        if request.get("op") == "status":
            return {"status": "ok", "used": self.used, "limit": self.limit,
                    "remaining": self.remaining, "closed": self.closed,
                    "expected_rule": self.expected_rule}
        if request.get("op") != "submit":
            return {"accepted": False, "error": "unknown operation", "used": self.used,
                    "limit": self.limit, "remaining": self.remaining}

        # Verification is serialized under this lock.  The budget check + append is atomic,
        # including if a future runner enables parallel agent sessions.
        with self._lock:
            if self._closed:
                return {"accepted": False, "exhausted": True, "closed": True,
                        "reason": f"rule episode already accepted: {self.expected_rule}",
                        "used": len(self._attempts), "limit": self.limit, "remaining": 0}
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
                    elif cert.get("rule") != self.expected_rule:
                        reason = (f"certificate rule {cert.get('rule')!r} does not match "
                                  f"this episode's rule {self.expected_rule!r}")
                    else:
                        try:
                            verdict = self._verifier(cert)
                            accepted = bool(verdict.accepted)
                            reason = verdict.reason
                            details = verdict.details
                        except Exception as e:  # infrastructure errors never debit the model
                            return {
                                "accepted": False,
                                "infrastructure_error": True,
                                "reason": f"verifier error: {type(e).__name__}: {e}",
                                "used": len(self._attempts),
                                "limit": self.limit,
                                "remaining": self.remaining,
                            }

            return self._append_attempt(cert, accepted, reason, details,
                                        request_id=request_id)

    def _record_rejection(self, reason: str, *, request_id: str | None = None) -> dict:
        with self._lock:
            if self._closed:
                return {"accepted": False, "exhausted": True, "closed": True,
                        "reason": f"rule episode already accepted: {self.expected_rule}",
                        "used": len(self._attempts), "limit": self.limit, "remaining": 0}
            if len(self._attempts) >= self.limit:
                return {"accepted": False, "exhausted": True, "reason": reason,
                        "used": self.limit, "limit": self.limit, "remaining": 0}
            return self._append_attempt(None, False, reason, request_id=request_id)

    def _append_attempt(self, cert, accepted: bool, reason: str, details=None,
                        request_id: str | None = None) -> dict:
        attempt_no = len(self._attempts) + 1
        record = {
            "attempt": attempt_no,
            "accepted": accepted,
            "rule": cert.get("rule") if isinstance(cert, dict) else None,
            "reason": reason,
            "certificate": cert,
        }
        if request_id is not None:
            record["request_id"] = request_id
        if details is not None:
            record["verify_details"] = details
        self._attempts.append(record)
        if accepted:
            self._closed = True
        return {**record, "used": attempt_no, "limit": self.limit,
                "remaining": 0 if self._closed else self.limit - attempt_no,
                "closed": self._closed}
