"""Shared thin-client transport for evaluation-owned agent commands."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

POLL_INTERVAL_SECONDS = 0.02


def spool_request(
    payload: dict,
    *,
    channel_env: str,
    timeout_env: str,
    default_timeout_seconds: int,
    service_name: str,
    request_id: str | None = None,
) -> dict:
    """Publish one idempotent request and await its atomic response."""
    channel = os.environ.get(channel_env)
    if not channel:
        raise RuntimeError(
            f"{service_name} is only available inside an active benchmark evaluation")
    request_id = request_id or os.environ.get("PRB_REQUEST_ID") or uuid.uuid4().hex
    if len(request_id) != 32 or any(char not in "0123456789abcdef" for char in request_id):
        raise ValueError("request id must be 32 lowercase hexadecimal characters")

    channel_dir = Path(channel)
    inbox = channel_dir / "inbox"
    outbox = channel_dir / "outbox"
    request_path = inbox / f"{request_id}.json"
    response_path = outbox / f"{request_id}.json"
    envelope = {"request_id": request_id, **payload}

    timeout = int(os.environ.get(timeout_env, default_timeout_seconds))
    attempts = int(os.environ.get("PRB_CLIENT_RETRIES", "1")) + 1
    for attempt in range(attempts):
        temporary = inbox / f".{request_id}.{os.getpid()}.{attempt}.tmp"
        temporary.write_text(json.dumps(envelope), encoding="utf-8")
        os.replace(temporary, request_path)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = response_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            response = json.loads(raw)
            try:
                response_path.unlink()
            except FileNotFoundError:
                pass
            return response
    raise TimeoutError(f"{service_name} did not respond within {timeout}s")
