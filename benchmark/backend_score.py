#!/usr/bin/env python3
"""
Backend scoring queue — the worker that turns PENDING submissions into ranked results.

The scoring is our zero-trust re-verification (benchmark/verify_submission.py → pred):

  * --local <subs_dir> <results_dir>
        Scan subs_dir/*.json, score each unprocessed submission, write the scored
        results.json + a <stem>.status.json (PENDING→RUNNING→FINISHED/FAILED), and
        aggregate every FINISHED, non-test entry into <results_dir>/leaderboard.json.
        Deterministic, no network → unit-testable. Idempotent: a submission already
        FINISHED is skipped on re-run.

Run inside the docker image (it has pred). This is the scorer invoked by
.github/workflows/score-from-r2.yml after it pulls pending submissions from R2.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from benchmark.verify_submission import leaderboard_entry, score_submission

STATUS_SUFFIX = ".status.json"


def _assert_pred_version() -> None:
    """The backend is the authoritative verifier, so its pred must be the pinned version.
    Skip when no pred is on PATH (pred-free unit tests never verify real certificates) or
    when EXPECTED_PRED_VERSION is set empty; otherwise hard-fail on a mismatch."""
    if not shutil.which("pred"):
        return
    from benchmark.env_setup import verify_pred_version
    verify_pred_version("pred")  # raises ValueError on mismatch


# ── status helpers ────────────────────────────────────────────────────────────

def _status_path(sub_path: Path) -> Path:
    return sub_path.with_name(sub_path.stem + STATUS_SUFFIX)


def _read_status(sub_path: Path) -> dict | None:
    sp = _status_path(sub_path)
    if sp.exists():
        try:
            return json.loads(sp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _write_status(sub_path: Path, status: str, **extra) -> None:
    _status_path(sub_path).write_text(
        json.dumps({"submission": sub_path.name, "status": status, **extra}, indent=2),
        encoding="utf-8",
    )


def _pending_submissions(subs_dir: Path) -> list[Path]:
    """Submission files not yet FINISHED (status missing or non-terminal).

    Recursive: real submissions live under submissions/<handle>/<file>.json, so a
    top-level glob would miss them.
    """
    out = []
    for p in sorted(subs_dir.rglob("*.json")):
        if p.name.endswith(STATUS_SUFFIX):
            continue
        st = _read_status(p)
        if st and st.get("status") == "FINISHED":
            continue
        out.append(p)
    return out


# ── scoring one submission ────────────────────────────────────────────────────

def score_one(sub_path: Path, results_dir: Path, repo_dir: str | None = None) -> dict:
    """Score a single submission file. Returns its leaderboard entry.

    Writes the scored results.json (results.schema-shaped) to results_dir/<stem>.json and
    transitions the submission's status file PENDING→RUNNING→FINISHED (or FAILED).
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    _write_status(sub_path, "RUNNING")
    try:
        submission = json.loads(sub_path.read_text(encoding="utf-8"))
        scored, report = score_submission(submission, repo_dir)
        entry = leaderboard_entry(submission, scored)
        (results_dir / f"{sub_path.stem}.json").write_text(
            json.dumps(scored, indent=2), encoding="utf-8")
        _write_status(sub_path, "FINISHED",
                      model=scored["model"], bugs_found=scored["bugs_found"],
                      verdicts=report)
        return entry
    except Exception as e:  # any failure → FAILED status with the reason (user feedback)
        _write_status(sub_path, "FAILED", error=str(e))
        raise


# ── leaderboard aggregation ───────────────────────────────────────────────────

def _dedup_best(entries: list[dict]) -> list[dict]:
    """Keep the best entry per model (max bugs, tie-break efficiency), ranked desc."""
    best: dict[str, dict] = {}
    for e in entries:
        m = e["model"]
        cur = best.get(m)
        key = (e.get("bugs_found", 0), e.get("efficiency_bugs_per_ktok", 0.0))
        if cur is None or key > (cur.get("bugs_found", 0), cur.get("efficiency_bugs_per_ktok", 0.0)):
            best[m] = e
    return sorted(best.values(),
                  key=lambda e: (e.get("bugs_found", 0), e.get("efficiency_bugs_per_ktok", 0.0)),
                  reverse=True)


def aggregate_leaderboard(results_dir: Path) -> list[dict]:
    """Rebuild leaderboard.json from every scored result file in results_dir."""
    results_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for p in sorted(results_dir.glob("*.json")):
        if p.name == "leaderboard.json":
            continue
        try:
            scored = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if "results" not in scored or "model" not in scored:
            continue
        # Test submissions are scored + kept privately, but never published to the public
        # leaderboard — skip them here so an end-to-end test can't pollute production.
        if scored.get("test"):
            continue
        # Reconstruct a minimal submission view for the entry (budget_cap lives in the
        # scored file only if we put it there; default to RANKED 20 for scored results).
        sub_view = {"model": scored["model"], "budget_cap": scored.get("budget_cap", 20),
                    "submitted_by": scored.get("submitted_by")}
        entries.append(leaderboard_entry(sub_view, scored))
    ranked = _dedup_best(entries)
    (results_dir / "leaderboard.json").write_text(json.dumps(ranked, indent=2), encoding="utf-8")
    return ranked


# ── local queue ───────────────────────────────────────────────────────────────

def process_local(subs_dir: str, results_dir: str, repo_dir: str | None = None) -> list[dict]:
    """Score all pending submissions in subs_dir; return a per-submission summary."""
    _assert_pred_version()
    subs = Path(subs_dir)
    results = Path(results_dir)
    summary = []
    for sub_path in _pending_submissions(subs):
        try:
            entry = score_one(sub_path, results, repo_dir)
            summary.append({"submission": sub_path.name, "status": "FINISHED",
                            "model": entry["model"], "bugs_found": entry["bugs_found"]})
        except Exception as e:
            summary.append({"submission": sub_path.name, "status": "FAILED", "error": str(e)})
    aggregate_leaderboard(results)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backend scoring queue for submissions")
    parser.add_argument("--local", nargs=2, metavar=("SUBS_DIR", "RESULTS_DIR"),
                        required=True, help="Score submissions from a local directory")
    parser.add_argument("--repo-dir", default=None, help="problem-reductions repo (default: pred on PATH)")
    args = parser.parse_args()

    summary = process_local(args.local[0], args.local[1], args.repo_dir)

    for s in summary:
        line = f"{s['status']:8}  {s['submission']}"
        if s["status"] == "FINISHED":
            line += f"  → {s['model']}: {s['bugs_found']} verified bug(s)"
        else:
            line += f"  ({s.get('error', '')})"
        print(line)
    n_failed = sum(1 for s in summary if s["status"] == "FAILED")
    print(f"\n{sum(1 for s in summary if s['status'] == 'FINISHED')} scored, "
          f"{n_failed} failed")
    # A FAILED status is an infra/verification error (crash, pred error), NOT a legit
    # "no bug" verdict (that is FINISHED with bugs_found=0). Exit non-zero so the caller
    # (score-from-r2.yml) stops BEFORE archiving incoming/ → processed/ — an un-scored
    # submission must stay queued for retry, never be silently lost.
    if n_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
