#!/usr/bin/env python3
"""`prb submit` — the command-line submission client (no web UI).

Uploads a run's ``submission.json`` (produced by ``make run``) to the benchmark's
serverless intake endpoint, which deposits it into the PRIVATE submission store. The
submission body carries the answer key (certificate + trajectory), so it never goes to a
public repo — it travels over HTTPS to the endpoint, which holds the write token.

    export PRB_SUBMIT_URL=https://<your-worker>/submit
    export PRB_API_KEY=...
    python -m benchmark.submit --predictions out/submission.json
    # → prints the submission id returned by the endpoint

The client validates the file locally FIRST (valid JSON, required envelope fields, and —
mirroring submission.schema.json — a certificate + trajectory on every self-reported
bug_found row) so a malformed run fails fast, before it hits the endpoint or burns quota.
The endpoint re-checks and the backend re-verifies with pred regardless; this is just a
courtesy gate. Use ``--dry-run`` to validate without sending.

HTTP contract (endpoint side):
    POST <url>   Authorization: Bearer <key>   Content-Type: application/json
    body    = the submission.json object
    200/201 → {"submission_id": "...", "status": "accepted", ...}
    4xx/5xx → {"error": "..."}  (or a non-JSON body, surfaced as text)
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REQUIRED_ENVELOPE = ("schema_version", "model", "library_commit", "budget_cap",
                     "total_cost_usd", "total_tokens_k", "rules_tested", "results")


def load_submission(path: Path) -> dict:
    """Read + JSON-parse a submission file (raises on missing/invalid)."""
    return json.loads(path.read_text(encoding="utf-8"))


def validate_submission(sub: dict) -> list[str]:
    """Client-side courtesy check. Returns a list of problems ([] == ok).

    Mirrors submission.schema.json's hard requirement: a self-reported ``bug_found`` row
    must carry both a ``certificate`` and a ``trajectory`` (provenance) — else the backend
    would reject it, so catch it here before spending a submission.
    """
    problems: list[str] = []
    if not isinstance(sub, dict):
        return ["submission is not a JSON object"]
    for field in REQUIRED_ENVELOPE:
        if field not in sub:
            problems.append(f"missing required field: {field}")
    results = sub.get("results")
    if not isinstance(results, list):
        problems.append("results must be a list")
        return problems
    for i, row in enumerate(results):
        if not isinstance(row, dict):
            problems.append(f"results[{i}] is not an object")
            continue
        if row.get("result") == "bug_found":
            rule = row.get("rule", "?")
            if not row.get("certificate"):
                problems.append(f"results[{i}] ({rule}): bug_found row has no certificate")
            if not row.get("trajectory"):
                problems.append(f"results[{i}] ({rule}): bug_found row has no trajectory "
                                "(required as provenance)")
    return problems


def _post(url: str, api_key: str, payload: bytes, timeout: float = 60.0) -> tuple[int, dict | str]:
    """POST raw JSON bytes with a bearer token. Returns (status_code, parsed-or-text body).

    Isolated so tests can monkeypatch the single network call.
    """
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}",
                 "User-Agent": "prb-submit"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, _maybe_json(body)
    except urllib.error.HTTPError as e:  # non-2xx still carries a useful body
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return e.code, _maybe_json(body)


def _maybe_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def submit(path: Path, url: str, api_key: str, *, dry_run: bool = False,
           timeout: float = 60.0, mark_test: bool = False) -> dict:
    """Validate and (unless dry_run) upload a submission. Returns a result dict.

    Raises ValueError on local validation failure or a non-2xx endpoint response, so the
    CLI can exit non-zero. ``mark_test`` stamps ``test: true`` so the backend scores and
    stores the submission privately but excludes it from the public leaderboard.
    """
    sub = load_submission(path)
    problems = validate_submission(sub)
    if problems:
        raise ValueError("submission failed local validation:\n  - " + "\n  - ".join(problems))
    if mark_test:
        sub["test"] = True

    bugs = sum(1 for r in sub.get("results", []) if r.get("result") == "bug_found")
    if dry_run:
        return {"status": "dry-run (not sent)", "model": sub.get("model"),
                "claimed_bugs": bugs, "bytes": len(path.read_bytes())}

    if not url:
        raise ValueError("no endpoint URL — set PRB_SUBMIT_URL or pass --url")
    if not api_key:
        raise ValueError("no API key — set PRB_API_KEY or pass --api-key")

    payload = json.dumps(sub).encode("utf-8")
    status, body = _post(url, api_key, payload, timeout=timeout)
    if not (200 <= status < 300):
        reason = body.get("error") if isinstance(body, dict) else body
        raise ValueError(f"endpoint returned HTTP {status}: {reason}")
    return body if isinstance(body, dict) else {"status": "accepted", "response": body}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="prb submit", description="Upload a submission.json to the benchmark intake endpoint")
    parser.add_argument("--predictions", "--file", dest="predictions", required=True,
                        help="Path to submission.json (from `make run`)")
    parser.add_argument("--url", default=os.environ.get("PRB_SUBMIT_URL"),
                        help="Intake endpoint URL (env PRB_SUBMIT_URL)")
    parser.add_argument("--api-key", default=os.environ.get("PRB_API_KEY"),
                        help="Bearer token for the endpoint (env PRB_API_KEY)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate the file locally and report what would be sent; no upload")
    parser.add_argument("--test", action="store_true",
                        help="Mark as a TEST submission: scored + stored privately but kept "
                             "out of the public leaderboard (for end-to-end checks)")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout seconds")
    args = parser.parse_args()

    path = Path(args.predictions)
    if not path.exists():
        parser.error(f"no such file: {path}")

    try:
        result = submit(path, args.url, args.api_key, dry_run=args.dry_run,
                        timeout=args.timeout, mark_test=args.test)
    except (ValueError, json.JSONDecodeError, OSError) as e:
        print(f"✗ {e}", file=sys.stderr)
        raise SystemExit(1)

    tag = " [TEST — excluded from public board]" if args.test else ""
    if args.dry_run:
        print(f"✓ valid — {result['model']}: {result['claimed_bugs']} claimed bug(s), "
              f"{result['bytes']} bytes (dry-run, not sent){tag}")
    else:
        sid = result.get("submission_id", "(no id returned)")
        print(f"✓ submitted — id {sid} ({result.get('status', 'accepted')}){tag}")
        print("The backend re-verifies every certificate with pred; only the aggregate "
              "(counts, no rules/certs) becomes public.")


if __name__ == "__main__":
    main()
