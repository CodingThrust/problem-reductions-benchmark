import json
import math
from pathlib import Path

import leaderboard as lb


def _results():
    return [
        {"model": "m-rank-a", "budget_cap": 20, "bugs_found": 3, "rules_tested": 30,
         "total_cost_usd": 19.8, "total_tokens_k": 420.0, "efficiency_bugs_per_ktok": 0.0071},
        {"model": "m-rank-b", "budget_cap": 20, "bugs_found": 2, "rules_tested": 45,
         "total_cost_usd": 20.0, "total_tokens_k": 510.0, "efficiency_bugs_per_ktok": 0.0039},
        {"model": "m-demo", "bugs_found": 9, "rules_tested": 5,
         "total_cost_usd": 0.4, "total_tokens_k": 50.0, "efficiency_bugs_per_ktok": 0.18},
    ]


def test_budget_reach_fraction():
    assert lb.budget_reach(30, 253) == 30 / 253
    assert lb.budget_reach(0, 253) == 0.0

def test_budget_reach_clamped_and_safe():
    assert lb.budget_reach(300, 253) == 1.0      # never exceeds 1
    assert lb.budget_reach(5, 0) == 0.0          # no divide-by-zero

def test_split_ranked_vs_demo():
    ranked, demo = lb.split_results(_results())
    assert [r["model"] for r in ranked] == ["m-rank-a", "m-rank-b"]
    assert [r["model"] for r in demo] == ["m-demo"]   # missing budget_cap → demo

def test_leaderboard_frame_ranks_by_bugs():
    df = lb.leaderboard_frame(_results(), total=253)
    assert list(df["model"]) == ["m-rank-a", "m-rank-b"]  # demo excluded, bugs desc
    assert list(df["rank"]) == [1, 2]
    assert math.isclose(df.iloc[0]["budget_reach"], 30 / 253)

def test_leaderboard_frame_tiebreak_by_ktok():
    rows = [
        {"model": "lo", "budget_cap": 20, "bugs_found": 2, "rules_tested": 10,
         "total_cost_usd": 20, "total_tokens_k": 100, "efficiency_bugs_per_ktok": 0.01},
        {"model": "hi", "budget_cap": 20, "bugs_found": 2, "rules_tested": 10,
         "total_cost_usd": 20, "total_tokens_k": 100, "efficiency_bugs_per_ktok": 0.05},
    ]
    df = lb.leaderboard_frame(rows, total=253)
    assert list(df["model"]) == ["hi", "lo"]   # equal bugs → higher ktok first

def test_ranked_rows_order_matches_leaderboard_frame():
    """ranked_rows order must exactly match leaderboard_frame model column order."""
    results = _results()
    rows = lb.ranked_rows(results)
    df = lb.leaderboard_frame(results, total=253)
    assert [r["model"] for r in rows] == list(df["model"])

def test_ranked_rows_carries_bug_certificates():
    certs_a = [{"rule": "r1", "violation": "v1", "note": "n",
                "source_type": "S", "target_type": "T", "trajectory_file": None}]
    results = [
        {"model": "m-a", "budget_cap": 20, "bugs_found": 3, "rules_tested": 30,
         "total_cost_usd": 19.8, "total_tokens_k": 420.0, "efficiency_bugs_per_ktok": 0.0071,
         "bug_certificates": certs_a},
        {"model": "m-b", "budget_cap": 20, "bugs_found": 1, "rules_tested": 10,
         "total_cost_usd": 5.0, "total_tokens_k": 100.0, "efficiency_bugs_per_ktok": 0.01,
         "bug_certificates": []},
    ]
    rows = lb.ranked_rows(results)
    # Every returned dict must have bug_certificates key
    assert all("bug_certificates" in r for r in rows)
    # Highest bugs comes first; certificates preserved
    assert rows[0]["model"] == "m-a"
    assert rows[0]["bug_certificates"] == certs_a

def test_has_placeholder():
    assert lb.has_placeholder([{"placeholder": True}]) is True
    assert lb.has_placeholder([{"model": "x"}]) is False

def test_load_results_reads_bundled_file():
    path = Path(__file__).parent.parent / "data" / "results.json"
    rows = lb.load_results(str(path))
    assert len(rows) >= 1
    assert all("model" in r for r in rows)


def _tasks_fixture():
    return str(Path(__file__).parent / "fixtures" / "tasks_sample.jsonl")

def test_load_tasks_from_local_file():
    df = lb.load_tasks(local_file=_tasks_fixture())
    assert len(df) == 3
    assert set(["rule", "source", "target", "summary"]).issubset(df.columns)
    assert "bmf_ilp" in list(df["rule"])

def test_filter_tasks_by_source():
    df = lb.load_tasks(local_file=_tasks_fixture())
    out = lb.filter_tasks(df, source="BMF")
    assert list(out["rule"]) == ["bmf_ilp"]

def test_filter_tasks_by_query_matches_any_text():
    df = lb.load_tasks(local_file=_tasks_fixture())
    out = lb.filter_tasks(df, query="maxcut")   # case-insensitive, matches target/summary
    assert list(out["rule"]) == ["spinglass_maxcut"]

def test_filter_tasks_empty_filters_returns_all():
    df = lb.load_tasks(local_file=_tasks_fixture())
    assert len(lb.filter_tasks(df)) == 3
