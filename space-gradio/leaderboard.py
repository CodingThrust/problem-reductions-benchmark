"""Pure data layer for the leaderboard — NO gradio imports (unit-testable)."""
import json
import os

import pandas as pd

RANKED_BUDGET = 20            # USD: only runs at this cap are ranked
TOTAL_TASKS_DEFAULT = 253     # coverage denominator fallback if dataset unavailable

_FRAME_COLUMNS = [
    "rank", "model", "bugs_found", "budget_reach",
    "rules_tested", "total_cost_usd", "total_tokens_k", "efficiency_bugs_per_ktok",
]


def load_results(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def budget_reach(rules_tested: int, total: int) -> float:
    """Fraction of the task set a run reached before the budget ran out (0.0–1.0)."""
    if not total:
        return 0.0
    return min(rules_tested / total, 1.0)


def has_placeholder(results: list[dict]) -> bool:
    return any(r.get("placeholder") for r in results)


def split_results(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """(ranked, demo): ranked = rows run at the fixed budget cap; demo = everything else."""
    ranked = [r for r in results if r.get("budget_cap") == RANKED_BUDGET]
    demo = [r for r in results if r.get("budget_cap") != RANKED_BUDGET]
    return ranked, demo


def ranked_rows(results: list[dict]) -> list[dict]:
    """Full ranked result dicts (budget_cap==20 only), sorted bugs_found desc
    then efficiency_bugs_per_ktok desc.  Includes ``model`` and ``bug_certificates``."""
    ranked, _ = split_results(results)
    return sorted(
        ranked,
        key=lambda r: (r.get("bugs_found", 0), r.get("efficiency_bugs_per_ktok", 0.0)),
        reverse=True,
    )


def leaderboard_frame(results: list[dict], total: int) -> pd.DataFrame:
    """Ranked table: bugs_found desc, tie-break efficiency_bugs_per_ktok desc."""
    rows = []
    for i, r in enumerate(ranked_rows(results), start=1):
        rows.append({
            "rank": i,
            "model": r.get("model", "?"),
            "bugs_found": r.get("bugs_found", 0),
            "budget_reach": budget_reach(r.get("rules_tested", 0), total),
            "rules_tested": r.get("rules_tested", 0),
            "total_cost_usd": r.get("total_cost_usd", 0.0),
            "total_tokens_k": r.get("total_tokens_k", 0.0),
            "efficiency_bugs_per_ktok": r.get("efficiency_bugs_per_ktok", 0.0),
        })
    return pd.DataFrame(rows, columns=_FRAME_COLUMNS)


DATASET_REPO = "isPANN/problem-reductions-benchmarks"
TASKS_FILE = "problem_reductions_bugs.jsonl"

_TASK_COLUMNS = ["rule", "source", "target", "summary",
                 "overhead_num_vars", "overhead_num_constraints"]


def _read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_tasks(repo_id: str = DATASET_REPO, token: str | None = None,
               local_file: str | None = None) -> pd.DataFrame:
    """Load the 253-task set. Prefer a local file (dev/tests); else pull from the HF dataset."""
    path = local_file or os.environ.get("TASKS_FILE")
    if not path:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(repo_id=repo_id, filename=TASKS_FILE,
                               repo_type="dataset", token=token)
    df = pd.DataFrame(_read_jsonl(path))
    return df.reindex(columns=_TASK_COLUMNS)  # selects, orders, fills any missing column


def filter_tasks(df: pd.DataFrame, source: str | None = None,
                 target: str | None = None, query: str | None = None) -> pd.DataFrame:
    out = df
    if source:
        out = out[out["source"].str.contains(source, case=False, na=False)]
    if target:
        out = out[out["target"].str.contains(target, case=False, na=False)]
    if query:
        mask = out.apply(
            lambda row: query.lower() in " ".join(str(v) for v in row.values).lower(),
            axis=1,
        )
        out = out[mask]
    return out
