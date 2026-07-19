#!/usr/bin/env python3
"""`prb submit` — the command-line submission client (no web UI).

Uploads a run's ``submission.json`` (produced by ``make run``) to the benchmark's
serverless intake endpoint, which deposits it into the PRIVATE submission store. The
submission body carries the answer key (certificates + submit ledger), so it never goes to a
public repo — it travels over HTTPS to the endpoint, which holds the write token.

    export PRB_SUBMIT_URL=https://<your-worker>/submit
    export PRB_ACCESS_TOKEN="$(cloudflared access token -app=https://<your-worker>)"
    python -m benchmark.submit --predictions out/submission.json
    # → prints the submission id returned by the endpoint

The client validates the file locally FIRST (valid JSON, required envelope fields, and —
mirroring submission.schema.json — a valid bounded ledger for current submissions) so a
malformed run fails fast, before it hits the endpoint or burns quota.
The endpoint re-checks and the backend re-verifies with pred regardless; this is just a
courtesy gate. Use ``--dry-run`` to validate without sending.

HTTP contract (endpoint side):
    POST <url>   Cf-Access-Token: <app-scoped JWT>   Content-Type: application/json
    legacy:      Authorization: Bearer <PRB_API_KEY>
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

from benchmark.submit_ledger import submit_ledger_error

REQUIRED_ENVELOPE = ("model", "library_commit", "bugs_found", "total_tokens_k",
                     "rules_tested", "results")


def load_submission(path: Path) -> dict:
    """Read + JSON-parse a submission file (raises on missing/invalid)."""
    return json.loads(path.read_text(encoding="utf-8"))


def validate_submission(sub: dict) -> list[str]:
    """Client-side courtesy check. Returns a list of problems ([] == ok).

    New runs prove provenance through the bounded submit ledger. Legacy runs must carry a
    certificate plus a trajectory on the row or envelope.
    """
    problems: list[str] = []
    if not isinstance(sub, dict):
        return ["submission is not a JSON object"]
    for field in REQUIRED_ENVELOPE:
        if field not in sub:
            problems.append(f"missing required field: {field}")

    if "model" in sub and (not isinstance(sub["model"], str) or not sub["model"].strip()):
        problems.append("model must be a non-empty string")
    if "library_commit" in sub and not isinstance(sub["library_commit"], str):
        problems.append("library_commit must be a string")
    bugs_found = sub.get("bugs_found")
    if ("bugs_found" in sub
            and (not isinstance(bugs_found, int) or isinstance(bugs_found, bool)
                 or bugs_found < 0)):
        problems.append("bugs_found must be a non-negative integer")
    rules_tested = sub.get("rules_tested")
    if ("rules_tested" in sub
            and (not isinstance(rules_tested, int) or isinstance(rules_tested, bool)
                 or rules_tested < 0)):
        problems.append("rules_tested must be a non-negative integer")
    tokens_k = sub.get("total_tokens_k")
    if ("total_tokens_k" in sub
            and (not isinstance(tokens_k, (int, float)) or isinstance(tokens_k, bool)
                 or tokens_k < 0)):
        problems.append("total_tokens_k must be a non-negative number")

    usage_totals = sub.get("usage_totals")
    if usage_totals is not None:
        if not isinstance(usage_totals, dict):
            problems.append("usage_totals must be an object")
        else:
            for bucket in ("input", "output", "cache_read", "cache_write"):
                value = usage_totals.get(bucket, 0)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    problems.append(
                        f"usage_totals.{bucket} must be a non-negative integer")

    results = sub.get("results")
    if not isinstance(results, list):
        problems.append("results must be a list")
        return problems
    envelope_traj = bool(sub.get("trajectory"))
    for i, row in enumerate(results):
        if not isinstance(row, dict):
            problems.append(f"results[{i}] is not an object")
            continue
        if not isinstance(row.get("rule"), str) or not row.get("rule", "").strip():
            problems.append(f"results[{i}].rule must be a non-empty string")
        if not isinstance(row.get("result"), str):
            problems.append(f"results[{i}].result must be a string")
        row_tokens = row.get("tokens_k")
        if (not isinstance(row_tokens, (int, float)) or isinstance(row_tokens, bool)
                or row_tokens < 0):
            problems.append(f"results[{i}].tokens_k must be a non-negative number")
        certificate = row.get("certificate")
        if certificate is not None and not isinstance(certificate, dict):
            problems.append(f"results[{i}].certificate must be an object")
        if row.get("result") == "bug_found":
            rule = row.get("rule", "?")
            if not row.get("certificate"):
                problems.append(f"results[{i}] ({rule}): bug_found row has no certificate")
            if ("submit_log" not in sub
                    and not row.get("trajectory") and not envelope_traj):
                problems.append(f"results[{i}] ({rule}): bug_found row has no trajectory "
                                "(required as provenance — on the row or the envelope)")

    if ledger_problem := submit_ledger_error(sub):
        problems.append(ledger_problem)
    return problems


def _auth_headers(api_key: str | None, access_token: str | None) -> dict[str, str]:
    """Build one authentication header, preferring the scoped Access credential."""
    if access_token:
        return {"Cf-Access-Token": access_token}
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def _post(url: str, payload: bytes, auth_headers: dict[str, str],
          timeout: float = 60.0) -> tuple[int, dict | str]:
    """POST raw JSON bytes. Returns (status_code, parsed-or-text body).

    Isolated so tests can monkeypatch the single network call.
    """
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 **auth_headers,
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


def submit(path: Path, url: str | None, api_key: str | None = None, *,
           access_token: str | None = None, dry_run: bool = False,
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
    auth_headers = _auth_headers(api_key, access_token)
    if not auth_headers:
        raise ValueError("no intake credential — set PRB_ACCESS_TOKEN (preferred) or "
                         "PRB_API_KEY (legacy)")

    payload = json.dumps(sub).encode("utf-8")
    status, body = _post(url, payload, auth_headers, timeout=timeout)
    if not (200 <= status < 300):
        reason = body.get("error") if isinstance(body, dict) else body
        raise ValueError(f"endpoint returned HTTP {status}: {reason}")
    if not isinstance(body, dict):
        raise ValueError("endpoint returned a non-JSON response; Cloudflare Access login "
                         "may be required")
    if not body.get("submission_id"):
        raise ValueError("endpoint accepted the request but returned no submission_id")
    return body


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="prb submit", description="Upload a submission.json to the benchmark intake endpoint")
    parser.add_argument("--predictions", "--file", dest="predictions", required=True,
                        help="Path to submission.json (from `make run`)")
    parser.add_argument("--url", default=os.environ.get("PRB_SUBMIT_URL"),
                        help="Intake endpoint URL (env PRB_SUBMIT_URL)")
    parser.add_argument("--api-key", default=os.environ.get("PRB_API_KEY"),
                        help="Legacy bearer token (env PRB_API_KEY); prefer PRB_ACCESS_TOKEN")
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
        result = submit(path, args.url, args.api_key,
                        access_token=os.environ.get("PRB_ACCESS_TOKEN"), dry_run=args.dry_run,
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
        if args.test:
            print("The backend re-verifies every certificate with pred; this test submission "
                  "is stored privately and will not update the public leaderboard.")
        else:
            print("The backend re-verifies every certificate with pred; only the aggregate "
                  "(counts, no rules/certs) becomes public.")


if __name__ == "__main__":
    main()
