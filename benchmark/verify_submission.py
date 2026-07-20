#!/usr/bin/env python3
"""Re-verify and score one benchmark submission artifact."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark.top50_contract import score_top50_submission, top50_public_entry


def score_submission(submission: dict, repo_dir: str | None = None) -> tuple[dict, list[dict]]:
    """Stable scorer entrypoint for the benchmark's single submission protocol."""
    return score_top50_submission(submission, repo_dir)


def leaderboard_entry(submission: dict, scored: dict) -> dict:
    """Stable aggregate-only projection entrypoint."""
    return top50_public_entry(submission, scored)


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-verify and score a submission.json")
    parser.add_argument("submission", help="Path to submission.json")
    parser.add_argument("--repo-dir", default=None,
                        help="problem-reductions repo (default: image-owned source)")
    parser.add_argument("--out", default=None, help="Write the scored result here")
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
    print(f"verified distinct-rule bugs: {scored['verified_bugs']}")

    if args.out:
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(scored, indent=2), encoding="utf-8")
        print(f"Scored result → {destination}")


if __name__ == "__main__":
    main()
