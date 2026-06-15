"""
Validate all results/*.json files against results.schema.json.

Usage:
    python -m benchmark.validate_results [--results-dir results]

Exits 0 if all files pass, 1 if any fail — naming the missing/wrong fields.
"""

import argparse
import json
import sys
from pathlib import Path

from benchmark.build_index import _validate


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate results files against schema")
    parser.add_argument("--results-dir", default="results")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    schema_path = Path(__file__).parent / "results.schema.json"

    if not results_dir.exists():
        print(f"Results directory not found: {results_dir}")
        sys.exit(1)

    files = [p for p in sorted(results_dir.glob("*.json")) if p.name != "index.json"]
    if not files:
        print(f"No results files found in {results_dir}")
        sys.exit(1)

    all_ok = True
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"FAIL  {path.name}: invalid JSON: {e}")
            all_ok = False
            continue

        err = _validate(data, schema_path, path.name)
        if err:
            print(f"FAIL  {path.name}: {err}")
            all_ok = False
        else:
            print(f"OK    {path.name}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
