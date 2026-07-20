#!/usr/bin/env python3
"""Thin agent-facing client for the evaluation-owned pred gateway."""
from __future__ import annotations

import json
import os
import sys
from benchmark.agent_spool import spool_request

DEFAULT_REQUEST_TIMEOUT_SECONDS = 300


def _request(payload: dict, request_id: str | None = None) -> dict:
    return spool_request(
        payload,
        channel_env="PRB_PRED_DIR",
        timeout_env="PRB_PRED_TIMEOUT",
        default_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        service_name="pred gateway",
        request_id=request_id,
    )


def _format_status(budget: dict) -> str:
    pred = budget["pred_calls"]
    solve = budget["solve_calls"]
    return (f"pred calls: {pred['used']}/{pred['limit']} used, {pred['remaining']} remaining; "
            f"solve calls: {solve['used']}/{solve['limit']} used, "
            f"{solve['remaining']} remaining")


def main() -> None:
    args = sys.argv[1:]
    try:
        if args == ["--budget-status"]:
            response = _request({"op": "status", "cwd": os.getcwd()})
            print(_format_status(response["budget"]))
            return
        response = _request({"op": "pred", "args": args, "cwd": os.getcwd()})
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError, TimeoutError) as error:
        print(f"pred unavailable: {error}", file=sys.stderr)
        raise SystemExit(2)

    if response.get("stdout"):
        print(response["stdout"], end="" if response["stdout"].endswith("\n") else "\n")
    if response.get("stderr"):
        print(response["stderr"], end="" if response["stderr"].endswith("\n") else "\n",
              file=sys.stderr)
    raise SystemExit(int(response.get("returncode", 2)))


if __name__ == "__main__":
    main()
