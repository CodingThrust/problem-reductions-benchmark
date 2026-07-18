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
import datetime
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from benchmark.env_setup import pinned_commit
from benchmark.schema_version import LATEST_SUBMISSION_SCHEMA_VERSION
from benchmark.submit import validate_submission
from benchmark.verify_submission import leaderboard_entry, score_submission

STATUS_SUFFIX = ".status.json"
OFFICIAL_SCHEMA_VERSION = LATEST_SUBMISSION_SCHEMA_VERSION


class PermanentSubmissionError(ValueError):
    """A submission-data error that retrying the same object cannot fix."""

# One PUBLIC file per submission lives at site/results/<slug>.json, where the slug ties the
# file (and its PR branch) to that specific run: model + submission time + a short id. The
# slug is derived deterministically from the scored file, so re-scoring the same submission
# yields the same slug/branch/PR (idempotent — no duplicate PRs), while a different
# submission (even same model) is a distinct file reviewed and merged on its own.
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", str(text).lower()).strip("-") or "model"


def _submission_ts(scored: dict, stem: str) -> str:
    """Compact UTC timestamp for the submission. Prefer the submission's own ``created_at``;
    else the intake epoch (ms) that prefixes the R2 filename stem ``<epoch>-<uuid>``."""
    ca = scored.get("created_at")
    if ca:
        return (re.sub(r"[^0-9T]", "", str(ca))[:15]) or "unknown"
    epoch = stem.split("-", 1)[0]
    if epoch.isdigit():
        dt = datetime.datetime.fromtimestamp(int(epoch) / 1000, tz=datetime.timezone.utc)
        return dt.strftime("%Y%m%dT%H%M%S")
    return "unknown"


def _submission_id(stem: str) -> str:
    """Short id for the submission — the uuid part of ``<epoch>-<uuid>``, else a stem hash."""
    _, sep, uid = stem.partition("-")
    return uid[:8] if sep and uid else hashlib.sha1(stem.encode()).hexdigest()[:8]


def board_slug(scored: dict, stem: str) -> str:
    return f"{_slug(scored['model'])}--{_submission_ts(scored, stem)}--{_submission_id(stem)}"


def board_entry(scored: dict, stem: str) -> dict:
    """The public per-submission leaderboard entry, tagged with its time + id."""
    # leaderboard_entry reads only ``submitted_by`` from its first arg; the scored file
    # carries it when present, so it doubles as the submission view.
    entry = leaderboard_entry(scored, scored)
    entry["timestamp"] = _submission_ts(scored, stem)
    entry["submission_id"] = _submission_id(stem)
    return entry


def write_board_entries(results_dir: Path, board_dir: Path) -> list[str]:
    """Write one PUBLIC entry file per NON-test scored submission into ``board_dir``.

    ``board_dir/<slug>.json`` = the aggregate-only entry (no certs / rule identities). Test
    submissions are skipped, so they never produce a public file. Returns the slugs written.
    """
    board_dir.mkdir(parents=True, exist_ok=True)
    slugs = []
    for p in sorted(results_dir.glob("*.json")):
        if p.name == "leaderboard.json":
            continue
        try:
            scored = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if "results" not in scored or "model" not in scored or scored.get("test"):
            continue
        slug = board_slug(scored, p.stem)
        (board_dir / f"{slug}.json").write_text(
            json.dumps(board_entry(scored, p.stem), indent=2), encoding="utf-8")
        slugs.append(slug)
    return slugs


def build_board(entries_dir: Path) -> list[dict]:
    """Aggregate the per-submission entry files (site/results/*.json) into the ranked board
    (best run per model). This is what the deployed site/results.json is built from."""
    entries = []
    for p in sorted(Path(entries_dir).glob("*.json")):
        if p.name == "results.json":
            continue
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(e, dict) and "model" in e:
            entries.append(e)
    return _dedup_best(entries)


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
        if st:
            if st.get("status") == "FINISHED":
                continue
            if st.get("status") == "FAILED" and st.get("retryable") is False:
                continue
        out.append(p)
    return out


# ── scoring one submission ────────────────────────────────────────────────────

def _validate_for_scoring(submission: object, *, official: bool,
                          expected_commit: str | None) -> dict:
    """Validate the durable queue envelope before invoking ``pred``.

    Basic validation applies to local and production scoring so malformed input is a
    permanent per-object failure instead of an exception that jams the whole queue.  The
    official gate additionally fixes the schema and target commit, and rejects partial runs
    from the public leaderboard. Test submissions may be partial because they never publish.
    """
    if not isinstance(submission, dict):
        raise PermanentSubmissionError("submission is not a JSON object")

    problems = validate_submission(submission)

    if official:
        if submission.get("schema_version") != OFFICIAL_SCHEMA_VERSION:
            problems.append(
                f"official submissions require schema_version {OFFICIAL_SCHEMA_VERSION}")
        target_commit = expected_commit or pinned_commit()
        if submission.get("library_commit") != target_commit:
            problems.append(
                "library_commit does not match this benchmark round "
                f"(expected {target_commit})")
        if submission.get("run_error") and not submission.get("test"):
            problems.append(
                "partial run has run_error; resubmit a clean run or upload it with --test")

    if problems:
        # Preserve order while removing duplicate messages emitted by overlapping checks.
        unique = list(dict.fromkeys(problems))
        raise PermanentSubmissionError("; ".join(unique))
    return submission


def score_one(sub_path: Path, results_dir: Path, repo_dir: str | None = None, *,
              official: bool = False, expected_commit: str | None = None) -> dict:
    """Score a single submission file. Returns its leaderboard entry.

    Writes the scored results.json (results.schema-shaped) to results_dir/<stem>.json and
    transitions the submission's status file PENDING→RUNNING→FINISHED (or FAILED).
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    _write_status(sub_path, "RUNNING")
    try:
        try:
            raw_submission = json.loads(sub_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError) as e:
            raise PermanentSubmissionError(f"invalid submission JSON: {e}") from e
        submission = _validate_for_scoring(
            raw_submission, official=official, expected_commit=expected_commit)
        scored, report = score_submission(submission, repo_dir)
        entry = leaderboard_entry(submission, scored)
        (results_dir / f"{sub_path.stem}.json").write_text(
            json.dumps(scored, indent=2), encoding="utf-8")
        _write_status(sub_path, "FINISHED",
                      model=scored["model"], bugs_found=scored["bugs_found"],
                      verdicts=report)
        return entry
    except Exception as e:  # isolate permanent input failures from retryable infra failures
        retryable = not isinstance(e, PermanentSubmissionError)
        _write_status(sub_path, "FAILED", error=str(e), retryable=retryable)
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
        # leaderboard_entry reads only ``submitted_by`` from its first arg, which the
        # scored file carries when present — it doubles as the submission view.
        entries.append(leaderboard_entry(scored, scored))
    ranked = _dedup_best(entries)
    (results_dir / "leaderboard.json").write_text(json.dumps(ranked, indent=2), encoding="utf-8")
    return ranked


# ── local queue ───────────────────────────────────────────────────────────────

def process_local(subs_dir: str, results_dir: str, repo_dir: str | None = None, *,
                  official: bool = False,
                  expected_commit: str | None = None) -> list[dict]:
    """Score all pending submissions in subs_dir; return a per-submission summary."""
    _assert_pred_version()
    subs = Path(subs_dir)
    results = Path(results_dir)
    summary = []
    for sub_path in _pending_submissions(subs):
        try:
            entry = score_one(
                sub_path, results, repo_dir,
                official=official, expected_commit=expected_commit)
            summary.append({"submission": sub_path.name, "status": "FINISHED",
                            "model": entry["model"], "bugs_found": entry["bugs_found"]})
        except Exception as e:
            summary.append({
                "submission": sub_path.name,
                "status": "FAILED",
                "error": str(e),
                "retryable": not isinstance(e, PermanentSubmissionError),
            })
    aggregate_leaderboard(results)
    # Public per-submission entry files (one PR each downstream); test entries are excluded.
    write_board_entries(results, results / "board")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backend scoring queue for submissions")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--local", nargs=2, metavar=("SUBS_DIR", "RESULTS_DIR"),
                      help="Score submissions from a local directory")
    mode.add_argument("--build-board", nargs=2, metavar=("ENTRIES_DIR", "OUT_JSON"),
                      help="Aggregate per-submission entry files (site/results/*.json) into "
                           "a ranked leaderboard JSON (the deployed site/results.json)")
    parser.add_argument("--repo-dir", default=None, help="problem-reductions repo (default: pred on PATH)")
    parser.add_argument(
        "--official", action="store_true",
        help="Require the current schema, pinned library commit, and a clean non-test run")
    parser.add_argument(
        "--expected-commit", default=None,
        help="Override the official target commit (tests/operations; default: image pin)")
    args = parser.parse_args()

    if args.build_board:
        board = build_board(Path(args.build_board[0]))
        Path(args.build_board[1]).write_text(json.dumps(board, indent=2), encoding="utf-8")
        print(f"built {args.build_board[1]}: {len(board)} model(s)")
        return

    summary = process_local(
        args.local[0], args.local[1], args.repo_dir,
        official=args.official, expected_commit=args.expected_commit)

    for s in summary:
        line = f"{s['status']:8}  {s['submission']}"
        if s["status"] == "FINISHED":
            line += f"  → {s['model']}: {s['bugs_found']} verified bug(s)"
        else:
            line += f"  ({s.get('error', '')})"
        print(line)
    n_failed = sum(1 for s in summary if s["status"] == "FAILED")
    n_retryable = sum(
        1 for s in summary if s["status"] == "FAILED" and s.get("retryable", True))
    print(f"\n{sum(1 for s in summary if s['status'] == 'FINISHED')} scored, "
          f"{n_failed} failed ({n_retryable} retryable)")
    # Permanent input failures are quarantined object-by-object by the R2 workflow. Only a
    # retryable failure makes the process non-zero, so transient verifier/infra failures stay
    # in incoming/ while other submissions can still be persisted and published.
    if n_retryable:
        sys.exit(1)


if __name__ == "__main__":
    main()
