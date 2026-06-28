#!/usr/bin/env python3
"""Pre-merge structural check for submission files (no pred, no scoring).

Validates every submissions/**/*.json against benchmark/submission.schema.json so a
malformed submission is caught on the PR, before a maintainer merges it. Scoring itself
(the authoritative, pred-backed step) runs only after merge — this is just a fast gate.

Exit 1 if any file fails; prints a per-file report.
"""
import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft7Validator
except ImportError:
    print("jsonschema not installed (pip install jsonschema)", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "benchmark" / "submission.schema.json").read_text("utf-8"))
SUBMISSIONS = ROOT / "submissions"


def main() -> int:
    validator = Draft7Validator(SCHEMA)
    files = sorted(p for p in SUBMISSIONS.rglob("*.json"))
    if not files:
        print("No submission files found under submissions/ — nothing to validate.")
        return 0

    failures = 0
    for path in files:
        rel = path.relative_to(ROOT)
        try:
            data = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError as e:
            print(f"✗ {rel}: invalid JSON — {e}")
            failures += 1
            continue
        errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
        if errors:
            failures += 1
            print(f"✗ {rel}: {len(errors)} schema error(s)")
            for e in errors[:8]:
                loc = "/".join(str(p) for p in e.path) or "(root)"
                print(f"    - {loc}: {e.message}")
            continue
        # Advisory (not a failure): only budget_cap == 20 runs are ranked.
        note = "" if data.get("budget_cap") == 20 else "  [note: budget_cap != 20 — won't be ranked]"
        print(f"✓ {rel}{note}")

    print(f"\n{len(files) - failures}/{len(files)} valid.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
