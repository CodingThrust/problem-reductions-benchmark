#!/usr/bin/env python3
"""Guard: the PUBLIC aggregate leaderboard must never carry the answer key.

`site/results.json` is the only submission-derived artifact that reaches the public repo
+ GitHub Pages. On a fixed public library commit a pred-confirmed certificate counts
regardless of provenance, so a leaked certificate / source instance / buggy-rule identity
is a free answer key. This is the single highest-value egress guard (per the security
checklist): every publish path runs it, and it hard-fails if anything but aggregate fields
appears.

Each entry may carry ONLY these keys (superset of leaderboard_entry's output):
"""
import json
import sys
from pathlib import Path

ALLOWED_KEYS = {
    "model", "library_commit", "budget_cap", "bugs_found", "rules_tested",
    "total_cost_usd", "total_tokens_k", "efficiency_bugs_per_ktok",
    "efficiency_bugs_per_dollar", "submitted_by", "placeholder",
    # per-submission entry files (site/results/<slug>.json) also carry provenance tags
    "timestamp", "submission_id",
}
# Substrings that must never appear anywhere in the serialized aggregate — a belt behind
# the per-entry allowlist, in case the shape ever changes.
FORBIDDEN_SUBSTRINGS = ("certificate", "trajectory", "\"source\"", "\"bundle\"",
                        "target_config", "reject_reason", "verify_details")


def check(path: Path) -> list[str]:
    problems: list[str] = []
    raw = path.read_text(encoding="utf-8")
    for needle in FORBIDDEN_SUBSTRINGS:
        if needle in raw:
            problems.append(f"forbidden token {needle!r} present — an answer-key field leaked")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [f"invalid JSON: {e}"]
    # Accept either the built board (a list of entries) or one per-submission entry (an
    # object) — both are guarded the same way, key by key.
    if isinstance(data, dict):
        entries = [data]
    elif isinstance(data, list):
        entries = data
    else:
        return ["top-level value must be a leaderboard entry object or a list of them"]

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(f"entry {i}: not an object")
            continue
        extra = set(entry) - ALLOWED_KEYS
        if extra:
            problems.append(f"entry {i} ({entry.get('model', '?')}): "
                            f"disallowed keys {sorted(extra)} — aggregate must be counts only")
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_aggregate.py <results.json>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"✗ {path}: not found", file=sys.stderr)
        return 1
    problems = check(path)
    if problems:
        print(f"✗ {path}: aggregate egress guard FAILED")
        for p in problems:
            print(f"    - {p}")
        return 1
    print(f"✓ {path}: aggregate-only (no certificates / rule identities)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
