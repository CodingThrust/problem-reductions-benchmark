#!/usr/bin/env python3
"""
Backend scoring queue — the worker that turns PENDING submissions into ranked results.

Mirrors the Open-LLM-Leaderboard requests→results pattern, but the scoring is our
zero-trust re-verification (benchmark/verify_submission.py → pred). Two modes:

  * --local <subs_dir> <results_dir>
        Scan subs_dir/*.json, score each unprocessed submission, write the scored
        results.json + a <stem>.status.json (PENDING→RUNNING→FINISHED/FAILED), and
        aggregate every FINISHED entry into <results_dir>/leaderboard.json (the list
        the Gradio Space loads). Deterministic, no network → unit-testable. Idempotent:
        a submission already FINISHED is skipped on re-run.

  * --hf-submissions <repo> --hf-results <repo>
        Same loop over HF datasets via huggingface_hub (needs HF_TOKEN with write access
        to the results repo). Runs inside the benchmark image, where pred is available.

Run inside the docker image (it has pred). As a polling Space: wrap process_local /
process_hf in a `while True: ...; sleep(N)` loop.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from benchmark.verify_submission import leaderboard_entry, score_submission

STATUS_SUFFIX = ".status.json"


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


# ── HF dataset queue ──────────────────────────────────────────────────────────

def process_hf(subs_repo: str, results_repo: str, repo_dir: str | None = None,
               token: str | None = None) -> list[dict]:
    """Score pending submissions stored in a HF dataset repo, upload scored results.

    Requires huggingface_hub and a write token for results_repo. Not exercised in CI.
    """
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError as e:  # pragma: no cover - env without huggingface_hub
        raise RuntimeError("huggingface_hub is required for --hf mode") from e

    import tempfile
    token = token or os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN required for --hf mode (write access to results repo)")

    api = HfApi(token=token)
    with tempfile.TemporaryDirectory() as tmp:
        local = snapshot_download(repo_id=subs_repo, repo_type="dataset", token=token,
                                  local_dir=str(Path(tmp) / "subs"))
        results_dir = Path(tmp) / "results"
        summary = process_local(local, str(results_dir), repo_dir)
        # Upload scored results + the rebuilt leaderboard to the results repo root, and
        # the new status files back to the submissions repo (preserving submissions/<handle>/).
        api.upload_folder(folder_path=str(results_dir), repo_id=results_repo,
                          repo_type="dataset")
        api.upload_folder(folder_path=local, repo_id=subs_repo, repo_type="dataset",
                          allow_patterns=["*.status.json", "**/*.status.json"])
    return summary


# ── webhook → HF Job entry ────────────────────────────────────────────────────
# Event-driven replacement for a polling Space: register a HF webhook on the
# submissions dataset that fires an HF Job; the Job runs `backend_score --webhook`,
# which reads the delivery from WEBHOOK_PAYLOAD and re-runs the (idempotent) queue.
# Avoids an always-on Space (free Spaces auto-pause at 48h + ephemeral disk).

def parse_webhook_payload(payload: dict) -> dict:
    """Pull repo + event fields from a HF webhook payload (schema v3)."""
    repo = payload.get("repo") or {}
    event = payload.get("event") or {}
    return {
        "repo_id": repo.get("name"),
        "repo_type": repo.get("type"),
        "action": event.get("action"),
        "scope": event.get("scope"),
    }


def _is_content_change(scope) -> bool:
    """True for repo content events (a new/updated submission file).

    Ignores discussion/comment scopes ("discussion", "discussion.comment") so PR chatter
    doesn't trigger scoring; content events use the "repo.*" scope (e.g. "repo.content").
    """
    return bool(scope) and str(scope).startswith("repo")


def process_webhook(
    payload: dict | None = None,
    *,
    submissions_repo: str | None = None,
    results_repo: str | None = None,
    repo_dir: str | None = None,
    token: str | None = None,
    expected_secret: str | None = None,
    provided_secret: str | None = None,
) -> list[dict]:
    """Handle one HF webhook delivery (run inside an HF Job).

    Reads the payload from the arg or the WEBHOOK_PAYLOAD env var (HF injects it). On a
    content change to the submissions repo it re-runs the scoring queue via process_hf —
    idempotent, so only newly-PENDING submissions get scored. Returns the per-submission
    summary, or [] when the delivery is ignored (non-content event).

    Repos resolve from args, else env SUBMISSIONS_REPO / RESULTS_REPO; submissions_repo
    finally falls back to the repo named in the payload.
    """
    if payload is None:
        raw = os.environ.get("WEBHOOK_PAYLOAD")
        payload = json.loads(raw) if raw else {}

    expected = expected_secret if expected_secret is not None else os.environ.get("WEBHOOK_SECRET")
    if expected and provided_secret is not None and provided_secret != expected:
        raise PermissionError("webhook secret mismatch — refusing to score")

    info = parse_webhook_payload(payload)
    if not _is_content_change(info.get("scope")):
        return []

    subs = submissions_repo or os.environ.get("SUBMISSIONS_REPO") or info.get("repo_id")
    results = results_repo or os.environ.get("RESULTS_REPO")
    if not subs or not results:
        raise RuntimeError(
            "submissions + results repos required "
            "(args, or env SUBMISSIONS_REPO / RESULTS_REPO)")
    return process_hf(subs, results, repo_dir=repo_dir, token=token)


def register_webhook(submissions_repo: str, job_id: str, *,
                     secret: str | None = None, token: str | None = None):
    """One-off: register a HF webhook that fires HF Job `job_id` when the submissions
    dataset changes. Needs huggingface_hub + a token. Returns the created WebhookInfo.
    """
    try:
        from huggingface_hub import create_webhook
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("huggingface_hub is required to register a webhook") from e
    return create_webhook(
        job_id=job_id,
        watched=[{"type": "dataset", "name": submissions_repo}],
        domains=["repo"],
        secret=secret,
        token=token,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backend scoring queue for submissions")
    parser.add_argument("--local", nargs=2, metavar=("SUBS_DIR", "RESULTS_DIR"),
                        help="Score submissions from a local directory")
    parser.add_argument("--hf-submissions", help="HF dataset repo holding submissions")
    parser.add_argument("--hf-results", help="HF dataset repo to write scored results to")
    parser.add_argument("--webhook", action="store_true",
                        help="Webhook→Job mode: read WEBHOOK_PAYLOAD + SUBMISSIONS_REPO/"
                             "RESULTS_REPO env and score the changed submissions")
    parser.add_argument("--repo-dir", default=None, help="problem-reductions repo (default: pred on PATH)")
    args = parser.parse_args()

    if args.webhook:
        summary = process_webhook(submissions_repo=args.hf_submissions,
                                  results_repo=args.hf_results, repo_dir=args.repo_dir)
        if not summary:
            print("Webhook delivery ignored (not a content change).")
            return
    elif args.local:
        summary = process_local(args.local[0], args.local[1], args.repo_dir)
    elif args.hf_submissions and args.hf_results:
        summary = process_hf(args.hf_submissions, args.hf_results, args.repo_dir)
    else:
        parser.error("use --local SUBS_DIR RESULTS_DIR, --hf-submissions/--hf-results, or --webhook")

    for s in summary:
        line = f"{s['status']:8}  {s['submission']}"
        if s["status"] == "FINISHED":
            line += f"  → {s['model']}: {s['bugs_found']} verified bug(s)"
        else:
            line += f"  ({s.get('error', '')})"
        print(line)
    print(f"\n{sum(1 for s in summary if s['status'] == 'FINISHED')} scored, "
          f"{sum(1 for s in summary if s['status'] == 'FAILED')} failed")


if __name__ == "__main__":
    main()
