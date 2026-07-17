#!/usr/bin/env python3
"""Thin agent-facing client for the evaluation-owned submit service.

Requests cross the agent sandbox through an atomic file spool rooted in the agent's
scratch workspace.  This deliberately avoids Unix sockets and localhost networking,
which are blocked by common headless-agent sandboxes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

MAX_CERTIFICATE_BYTES = 1024 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 0.02


def _read_certificate(path: str) -> str:
    stream = sys.stdin.buffer if path == "-" else Path(path).open("rb")
    try:
        raw = stream.read(MAX_CERTIFICATE_BYTES + 1)
    finally:
        if path != "-":
            stream.close()
    if len(raw) > MAX_CERTIFICATE_BYTES:
        raise ValueError(f"certificate exceeds {MAX_CERTIFICATE_BYTES} bytes")
    return raw.decode("utf-8")


def _request(payload: dict) -> dict:
    channel = os.environ.get("PRB_SUBMIT_DIR")
    if not channel:
        raise RuntimeError("submit is only available inside an active benchmark evaluation")
    channel_dir = Path(channel)
    request_id = uuid.uuid4().hex
    inbox = channel_dir / "inbox"
    outbox = channel_dir / "outbox"
    request_path = inbox / f"{request_id}.json"
    temp_path = inbox / f".{request_id}.{os.getpid()}.tmp"
    response_path = outbox / f"{request_id}.json"

    envelope = {"request_id": request_id, **payload}
    temp_path.write_text(json.dumps(envelope), encoding="utf-8")
    os.replace(temp_path, request_path)

    timeout = int(os.environ.get("PRB_SUBMIT_TIMEOUT", DEFAULT_REQUEST_TIMEOUT_SECONDS))
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
    raise TimeoutError(f"submit service did not respond within {timeout}s")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="submit",
        description="Submit one rule counterexample to the benchmark verifier. Every "
                    "submission, accepted or rejected, consumes one run-wide attempt.",
    )
    parser.add_argument("certificate", nargs="?", help="Certificate JSON file, or - for stdin")
    parser.add_argument("--status", action="store_true", help="Show remaining attempts (free)")
    args = parser.parse_args()

    try:
        if args.status:
            if args.certificate:
                parser.error("--status does not take a certificate")
            response = _request({"op": "status"})
            print(f"submit budget: {response['used']}/{response['limit']} used, "
                  f"{response['remaining']} remaining")
            return
        if not args.certificate:
            parser.error("a certificate JSON file is required (use - for stdin)")
        try:
            text = _read_certificate(args.certificate)
            response = _request({"op": "submit", "certificate_text": text})
        except (OSError, UnicodeError, ValueError) as e:
            # File/encoding/size failures are still submission attempts; let the service
            # account for them instead of failing locally without consuming budget.
            response = _request({"op": "submit", "client_error": str(e)})
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as e:
        print(f"submit unavailable: {e}", file=sys.stderr)
        raise SystemExit(2)

    used, limit, remaining = response["used"], response["limit"], response["remaining"]
    if response.get("accepted"):
        print(f"ACCEPTED attempt {used}/{limit} ({remaining} remaining): {response.get('rule')}")
        return

    reason = response.get("reason") or response.get("error") or "rejected"
    label = "BUDGET_EXHAUSTED" if response.get("exhausted") else "REJECTED"
    print(f"{label} attempt {used}/{limit} ({remaining} remaining): {reason}", file=sys.stderr)
    raise SystemExit(2 if response.get("exhausted") else 1)


if __name__ == "__main__":
    main()
