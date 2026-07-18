#!/usr/bin/env python3
"""
Authoritative backend scorer for a submission.json.

Zero trust: re-runs the certificate verifier (benchmark/verify.py → pred) on every
claimed bug and recomputes the score from what pred actually confirms. The submission's
self-reported ``bugs_found`` is ignored entirely.

Produces two views of the result:
  * ``scored``          — results.schema.json-compatible (the backend's per-submission output)
  * ``leaderboard_entry`` — the public ranked-row shape (aggregate only: counts, tokens,
                            efficiency — never the certificates or buggy-rule identities,
                            which would be a free answer key)

CLI:
    python -m benchmark.verify_submission <submission.json> [--repo-dir <path>]
    # prints the per-certificate verdict report + the recomputed score; exits 0 always
    # (a submission with 0 confirmed bugs is a valid, scored result — not an error)
"""
import argparse
import json
import re
import sys
from pathlib import Path

from benchmark.submit_ledger import (accepted_certificate_index, certificate_key,
                                     has_submit_ledger, schema_requires_ledger,
                                     submit_ledger_error)
from benchmark.usage import usage_from_dict
from benchmark.verify import count_bugs, verify

CERT_BLOCK = re.compile(r"CERTIFICATE_START\s*\n(.*?)CERTIFICATE_END", re.DOTALL)


def _certs_from_trajectory(trajectory) -> list[dict]:
    """Parse every CERTIFICATE_START…END block emitted in an agent trajectory.

    ``trajectory`` is a list of {role, content} messages (as saved by the runner). A
    whole-repo run emits many certificates in one trajectory, so return all of them;
    unparseable blocks are skipped.
    """
    certs: list[dict] = []
    for msg in trajectory or []:
        content = msg.get("content", "") or ""
        for m in CERT_BLOCK.finditer(content):
            try:
                certs.append(json.loads(m.group(1).strip()))
            except json.JSONDecodeError:
                continue
    return certs


def _provenance_ok(row: dict, cert: dict, session_certs: list[dict] = ()) -> tuple[bool, str]:
    """Check the certificate was actually produced by this model's own run.

    Guards against copied answer keys: a scored bug must appear as a CERTIFICATE block the
    agent emitted in its trajectory, matching both rule and source instance. The trajectory
    is either the legacy row's own or the shared legacy session log at the envelope level —
    ``session_certs`` is that envelope
    log pre-parsed once. Any emitted block that matches counts. This can't make copying
    impossible (the library is public), but it lifts the bar from "paste a rule name" to
    "produce a run artifact whose source still round-trip-fails".
    """
    if row.get("rule") != cert.get("rule"):
        return False, "result row rule does not match its certificate rule"
    emitted = _certs_from_trajectory(row.get("trajectory")) + list(session_certs)
    if not emitted:
        return False, "no trajectory attached (required for a scored bug)"
    for e in emitted:
        if e.get("rule") == cert.get("rule") and e.get("source") == cert.get("source"):
            return True, "reproduced in the model's own trajectory"
    return False, "no trajectory certificate matches the submitted rule + source"


def score_submission(submission: dict, repo_dir: str | None = None) -> tuple[dict, list[dict]]:
    """Re-verify every certificate and recompute the score.

    A bug counts only when pred confirms the round-trip failure and provenance succeeds:
    bounded submit ledger for schema 2.1+, trajectory certificate for legacy submissions.
    Returns (scored, report); ``scored`` is results.schema.json-shaped and ``report`` is a
    per-certificate list of {rule, violation, accepted, reason, provenance}.
    """
    rescored: list[dict] = []
    report: list[dict] = []
    ledger_problem = submit_ledger_error(submission)
    uses_ledger = (has_submit_ledger(submission)
                   or schema_requires_ledger(submission.get("schema_version")))
    accepted_keys = (accepted_certificate_index(submission)
                     if uses_ledger and ledger_problem is None else set())
    # Only legacy submissions use trajectory provenance. New runs use the ledger directly.
    session_certs = ([] if uses_ledger else
                     _certs_from_trajectory(submission.get("trajectory")))

    for row in submission.get("results", []):
        cert = row.get("certificate")
        if not cert:
            rescored.append(row)
            continue

        cert.setdefault("rule", row.get("rule"))
        verdict = verify(cert, repo_dir)
        if verdict.accepted and uses_ledger:
            if ledger_problem is not None:
                prov_ok, prov_reason = False, ledger_problem
            elif row.get("rule") != cert.get("rule"):
                prov_ok, prov_reason = False, "result row rule does not match its certificate rule"
            else:
                prov_ok = certificate_key(row["rule"], cert) in accepted_keys
                prov_reason = ("accepted through the bounded submit command" if prov_ok else
                               "certificate was not accepted through the bounded submit command")
        elif verdict.accepted:
            prov_ok, prov_reason = _provenance_ok(row, cert, session_certs)
        else:
            prov_ok, prov_reason = False, ""
        accepted = verdict.accepted and prov_ok
        new = dict(row)
        if accepted:
            new["result"] = "bug_found"
            new["verify_details"] = verdict.details
            new.pop("reject_reason", None)
        else:
            new["result"] = "rejected"
            new["reject_reason"] = (
                verdict.reason if not verdict.accepted else f"provenance: {prov_reason}")
            new.pop("verify_details", None)
        rescored.append(new)
        report.append({
            "rule": row.get("rule"),
            "violation": cert.get("violation"),
            "accepted": accepted,
            "reason": new.get("reject_reason") if not accepted else verdict.reason,
            "provenance": prov_reason if verdict.accepted else None,
        })

    bugs = count_bugs(rescored)
    # Token totals: recompute from the 4-bucket ``usage_totals`` when present (the
    # reproducible primitive); legacy submissions without it fall back to their
    # self-reported total_tokens_k.
    total_tokens = usage_from_dict(submission.get("usage_totals")).total_tokens
    tokens_k = total_tokens / 1000 if total_tokens else (submission.get("total_tokens_k", 0.0) or 0.0)
    scored = {
        "schema_version": submission.get("schema_version"),
        "model": submission.get("model", "unknown"),
        "library_commit": submission.get("library_commit", "unknown"),
        # Test submissions (end-to-end checks) are scored and stored privately like any
        # other, but aggregate_leaderboard() excludes them so they never reach the public
        # board. Carried through here so the flag survives in the private scored file.
        "test": bool(submission.get("test")),
        "bugs_found": bugs,
        "total_tokens_k": round(tokens_k, 2),
        # The reproducible primitive behind total_tokens_k — carried through.
        "usage_totals": submission.get("usage_totals"),
        "efficiency_bugs_per_ktok": round(bugs / tokens_k, 4) if tokens_k else 0,
        "rules_tested": submission.get("rules_tested", len(rescored)),
        "results": rescored,
        "submit_limit": submission.get("submit_limit"),
        "submit_log": submission.get("submit_log"),
    }
    # Private operational/provenance metadata must survive scoring. In particular,
    # backend_score rebuilds public entries from these scored files later, so dropping
    # submitted_by here silently erased the submitter. run_error remains private and is used
    # by the official intake gate; test runs may retain it for diagnosis.
    for key in ("submitted_by", "created_at", "run_error", "pred_version", "agent_mode"):
        if key in submission:
            scored[key] = submission[key]
    return scored, report


def leaderboard_entry(submission: dict, scored: dict) -> dict:
    """Build the public ranked-row entry from a scored result.

    Aggregate-only, by design: it carries counts, token totals and efficiency but NEVER
    the certificates or the identities of the buggy rules. Publishing those would be a
    free answer key — on a public library commit a `pred`-confirmed certificate counts
    regardless of provenance, so anyone could copy it. The full certificates stay in the
    private scored result the maintainer holds; the leaderboard shows only how many each
    model found.
    """
    return {
        "model": scored["model"],
        "library_commit": scored.get("library_commit", "unknown"),
        "bugs_found": scored["bugs_found"],
        "rules_tested": scored["rules_tested"],
        "total_tokens_k": scored["total_tokens_k"],
        # Aggregate token totals — safe to publish (no rule identities).
        "usage_totals": scored.get("usage_totals"),
        "efficiency_bugs_per_ktok": scored["efficiency_bugs_per_ktok"],
        "submitted_by": submission.get("submitted_by"),
        "placeholder": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-verify and score a submission.json")
    parser.add_argument("submission", help="Path to submission.json")
    parser.add_argument("--repo-dir", default=None, help="problem-reductions repo (default: pred on PATH)")
    parser.add_argument("--out", default=None, help="Write the scored results.json here")
    args = parser.parse_args()

    submission = json.loads(Path(args.submission).read_text(encoding="utf-8"))
    scored, report = score_submission(submission, args.repo_dir)

    print(f"Scoring {scored['model']}")
    print("-" * 60)
    for item in report:
        flag = "✓ ACCEPTED" if item["accepted"] else "✗ rejected"
        print(f"{flag}  {item['rule']} [{item['violation']}]")
        print(f"           {item['reason']}")
    print("-" * 60)
    print(f"self-reported bugs: {submission.get('bugs_found')!r}  →  "
          f"verified distinct-rule bugs: {scored['bugs_found']}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(scored, indent=2), encoding="utf-8")
        print(f"Scored result → {args.out}")


if __name__ == "__main__":
    main()
