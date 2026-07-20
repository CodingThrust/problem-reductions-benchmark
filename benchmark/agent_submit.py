#!/usr/bin/env python3
"""Thin agent-facing client for the evaluation-owned submit service.

Requests cross the agent sandbox through an atomic file spool rooted in the agent's
scratch workspace.  This deliberately avoids Unix sockets and localhost networking,
which are blocked by the benchmark agent sandbox.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmark.agent_spool import spool_request

MAX_CERTIFICATE_BYTES = 1024 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300


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


def _request(payload: dict, request_id: str | None = None) -> dict:
    return spool_request(
        payload,
        channel_env="PRB_SUBMIT_DIR",
        timeout_env="PRB_SUBMIT_TIMEOUT",
        default_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        service_name="submit service",
        request_id=request_id,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="submit",
        description="Submit one rule counterexample to the benchmark verifier. Every "
                    "submission, accepted or rejected, consumes one rule-scoped attempt.",
    )
    parser.add_argument("certificate", nargs="?", help="Certificate JSON file, or - for stdin")
    parser.add_argument("--status", action="store_true", help="Show remaining attempts (free)")
    parser.add_argument("--request-id", help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        if args.status:
            if args.certificate:
                parser.error("--status does not take a certificate")
            response = _request({"op": "status"}, args.request_id)
            print(f"submit budget: {response['used']}/{response['limit']} used, "
                  f"{response['remaining']} remaining")
            return
        if not args.certificate:
            parser.error("a certificate JSON file is required (use - for stdin)")
        try:
            text = _read_certificate(args.certificate)
            response = _request({"op": "submit", "certificate_text": text}, args.request_id)
        except (OSError, UnicodeError, ValueError) as e:
            # File/encoding/size failures are still submission attempts; let the service
            # account for them instead of failing locally without consuming budget.
            response = _request({"op": "submit", "client_error": str(e)}, args.request_id)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError, TimeoutError) as e:
        print(f"submit unavailable: {e}", file=sys.stderr)
        raise SystemExit(2)

    used, limit, remaining = response["used"], response["limit"], response["remaining"]
    if response.get("accepted"):
        print(f"ACCEPTED attempt {used}/{limit} ({remaining} remaining): {response.get('rule')}")
        return

    reason = response.get("reason") or response.get("error") or "rejected"
    label = ("INFRASTRUCTURE_ERROR" if response.get("infrastructure_error") else
             "BUDGET_EXHAUSTED" if response.get("exhausted") else "REJECTED")
    print(f"{label} attempt {used}/{limit} ({remaining} remaining): {reason}", file=sys.stderr)
    raise SystemExit(2 if response.get("exhausted") or response.get("infrastructure_error") else 1)


if __name__ == "__main__":
    main()
