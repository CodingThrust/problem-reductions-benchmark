#!/usr/bin/env python3
"""
Authoritative backend scorer for a submission.json.

Zero trust: re-runs the certificate verifier (benchmark/verify.py → pred) on every
claimed bug and recomputes the score from what pred actually confirms. The submission's
self-reported ``bugs_found`` is ignored entirely.

Produces two views of the result:
  * ``scored``          — results.schema.json-compatible (the backend's per-submission output)
  * ``leaderboard_entry`` — the public ranked-row shape (aggregate only: counts, cost,
                            efficiency, budget_cap — never the certificates or buggy-rule
                            identities, which would be a free answer key)

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

from benchmark.verify import count_bugs, verify

CERT_BLOCK = re.compile(r"CERTIFICATE_START\s*\n(.*?)CERTIFICATE_END", re.DOTALL)


def _cert_from_trajectory(trajectory) -> dict | None:
    """Parse the last CERTIFICATE_START…END block emitted in an agent trajectory.

    ``trajectory`` is a list of {role, content} messages (as saved by the runner).
    Returns the parsed certificate dict, or None if absent/unparseable.
    """
    if not trajectory:
        return None
    for msg in reversed(trajectory):
        content = msg.get("content", "") or ""
        m = CERT_BLOCK.search(content)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                return None
    return None


def _provenance_ok(row: dict, cert: dict) -> tuple[bool, str]:
    """Check the certificate was actually produced by this model's own run.

    Guards against copied answer keys: a scored bug must appear in a CERTIFICATE block the
    agent emitted in its trajectory, with the same rule and source instance. This can't
    make copying impossible (the target library is public), but it lifts the bar from
    "paste a rule name" to "produce a full run artifact whose source still round-trip-fails
    under pred".
    """
    traj = row.get("trajectory")
    if not traj:
        return False, "no trajectory attached (required for a scored bug)"
    emitted = _cert_from_trajectory(traj)
    if emitted is None:
        return False, "no CERTIFICATE block found in trajectory"
    if emitted.get("rule") not in (cert.get("rule"), row.get("rule")):
        return False, "trajectory certificate targets a different rule"
    if emitted.get("source") != cert.get("source"):
        return False, "trajectory certificate source does not match the submitted source"
    return True, "reproduced in the model's own trajectory"


def score_submission(submission: dict, repo_dir: str | None = None) -> tuple[dict, list[dict]]:
    """Re-verify every certificate and recompute the score.

    A bug counts only when BOTH hold: pred confirms the round-trip failure (zero-trust
    re-derivation) AND the certificate is reproduced in the model's own trajectory
    (provenance). Returns (scored, report); ``scored`` is results.schema.json-shaped and
    ``report`` is a per-certificate list of {rule, violation, accepted, reason, provenance}.
    """
    rescored: list[dict] = []
    report: list[dict] = []

    for row in submission.get("results", []):
        cert = row.get("certificate")
        if not cert:
            rescored.append(row)
            continue

        cert.setdefault("rule", row.get("rule"))
        verdict = verify(cert, repo_dir)
        prov_ok, prov_reason = _provenance_ok(row, cert) if verdict.accepted else (False, "")
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
    tokens_k = submission.get("total_tokens_k", 0.0) or 0.0
    cost = submission.get("total_cost_usd", 0.0) or 0.0
    attempted = [r for r in rescored if r.get("result") != "skipped_budget"]
    scored = {
        "model": submission.get("model", "unknown"),
        "library_commit": submission.get("library_commit", "unknown"),
        # Test submissions (end-to-end checks) are scored and stored privately like any
        # other, but aggregate_leaderboard() excludes them so they never reach the public
        # board. Carried through here so the flag survives in the private scored file.
        "test": bool(submission.get("test")),
        "bugs_found": bugs,
        "total_cost_usd": cost,
        "total_tokens_k": tokens_k,
        "efficiency_bugs_per_ktok": round(bugs / tokens_k, 4) if tokens_k else 0,
        "efficiency_bugs_per_dollar": round(bugs / cost, 4) if cost else 0,
        "rules_tested": submission.get("rules_tested", len(attempted)),
        "results": rescored,
    }
    return scored, report


def leaderboard_entry(submission: dict, scored: dict) -> dict:
    """Build the public ranked-row entry from a scored result.

    Aggregate-only, by design: it carries counts, cost/token totals, efficiency and
    ``budget_cap`` (rows with budget_cap == 20 are ranked) but NEVER the certificates or
    the identities of the buggy rules. Publishing those would be a free answer key — on a
    public library commit a `pred`-confirmed certificate counts regardless of provenance,
    so anyone could copy it. The full certificates stay in the private scored result the
    maintainer holds; the leaderboard shows only how many each model found.
    """
    return {
        "model": scored["model"],
        "library_commit": scored.get("library_commit", "unknown"),
        "budget_cap": submission.get("budget_cap"),
        "bugs_found": scored["bugs_found"],
        "rules_tested": scored["rules_tested"],
        "total_cost_usd": scored["total_cost_usd"],
        "total_tokens_k": scored["total_tokens_k"],
        "efficiency_bugs_per_ktok": scored["efficiency_bugs_per_ktok"],
        "efficiency_bugs_per_dollar": scored["efficiency_bugs_per_dollar"],
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

    print(f"Scoring {scored['model']} (budget ${submission.get('budget_cap')})")
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
