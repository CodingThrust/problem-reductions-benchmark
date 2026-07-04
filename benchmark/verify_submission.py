#!/usr/bin/env python3
"""
Authoritative backend scorer for a submission.json.

Zero trust: re-runs the certificate verifier (benchmark/verify.py → pred) on every
claimed bug and recomputes the score from what pred actually confirms. The submission's
self-reported ``bugs_found`` is ignored entirely.

Produces two views of the result:
  * ``scored``          — results.schema.json-compatible (the backend's per-submission output)
  * ``leaderboard_entry`` — the Space's ranked-row shape (adds budget_cap + bug_certificates)

CLI:
    python -m benchmark.verify_submission <submission.json> [--repo-dir <path>]
    # prints the per-certificate verdict report + the recomputed score; exits 0 always
    # (a submission with 0 confirmed bugs is a valid, scored result — not an error)
"""
import argparse
import json
import sys
from pathlib import Path

from benchmark.verify import count_bugs, verify


def score_submission(submission: dict, repo_dir: str | None = None) -> tuple[dict, list[dict]]:
    """Re-verify every certificate and recompute the score.

    Returns (scored, report). ``scored`` is results.schema.json-shaped; ``report`` is a
    per-certificate list of {rule, violation, accepted, reason}.
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
        new = dict(row)
        if verdict.accepted:
            new["result"] = "bug_found"
            new["verify_details"] = verdict.details
            new.pop("reject_reason", None)
        else:
            new["result"] = "rejected"
            new["reject_reason"] = verdict.reason
            new.pop("verify_details", None)
        rescored.append(new)
        report.append({
            "rule": row.get("rule"),
            "violation": cert.get("violation"),
            "accepted": verdict.accepted,
            "reason": verdict.reason,
        })

    bugs = count_bugs(rescored)
    tokens_k = submission.get("total_tokens_k", 0.0) or 0.0
    cost = submission.get("total_cost_usd", 0.0) or 0.0
    attempted = [r for r in rescored if r.get("result") != "skipped_budget"]
    scored = {
        "model": submission.get("model", "unknown"),
        "library_commit": submission.get("library_commit", "unknown"),
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
    """Build the Space's ranked-row entry from a scored result.

    Carries ``budget_cap`` (the Space ranks rows with budget_cap == 20) and the
    confirmed-bug certificate drilldown.
    """
    bug_certs = []
    for r in scored.get("results", []):
        if r.get("result") != "bug_found" or not r.get("certificate"):
            continue
        cert = r["certificate"]
        bug_certs.append({
            "rule": r["rule"],
            "violation": cert.get("violation"),
            "note": cert.get("note", ""),
            "source_type": cert.get("source", {}).get("type"),
            "target_type": cert.get("bundle", {}).get("target", {}).get("type"),
            "trajectory_file": None,
        })
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
        "bug_certificates": bug_certs,
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
