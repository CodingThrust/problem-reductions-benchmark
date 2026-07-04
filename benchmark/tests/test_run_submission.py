"""
Tests for benchmark/run_submission.py — the dockerized runner entry point.

All tests run in FAKE mode (FakeRunner): no model API, no pred binary. They prove the
runner assembles a schema-valid, rankable submission.json and respects the $-budget cap.
"""
import json
from pathlib import Path

import pytest

from benchmark import run_submission as rs


def _fake_repo(tmp_path: Path, rules: list[str]) -> Path:
    repo = tmp_path / "pr-src"
    rules_dir = repo / "src" / "rules"
    rules_dir.mkdir(parents=True)
    for r in rules:
        (rules_dir / f"{r}.rs").write_text("// dummy rule\n", encoding="utf-8")
    return repo


# ── build_submission (pure assembly) ──────────────────────────────────────────

class TestBuildSubmission:
    def test_envelope_fields_present(self):
        rows = [{"rule": "r1", "result": "no_certificate", "cost": 0.1, "tokens_k": 2.0}]
        sub = rs.build_submission("anthropic/x", rows, budget_cap=20.0,
                                  library_commit="abc123")
        for k in ("schema_version", "model", "library_commit", "budget_cap",
                  "bugs_found", "total_cost_usd", "total_tokens_k", "rules_tested",
                  "results", "efficiency_bugs_per_ktok", "efficiency_bugs_per_dollar"):
            assert k in sub
        assert sub["budget_cap"] == 20.0
        assert sub["model"] == "anthropic/x"
        assert sub["library_commit"] == "abc123"

    def test_bugs_counted_distinct_rules(self):
        rows = [
            {"rule": "r1", "result": "bug_found", "cost": 0.1, "tokens_k": 1.0,
             "certificate": {"rule": "r1", "violation": "solve_mismatch"}},
            {"rule": "r1", "result": "bug_found", "cost": 0.1, "tokens_k": 1.0,
             "certificate": {"rule": "r1", "violation": "unsound_extraction"}},
            {"rule": "r2", "result": "bug_found", "cost": 0.1, "tokens_k": 1.0,
             "certificate": {"rule": "r2", "violation": "solve_mismatch"}},
            {"rule": "r3", "result": "no_certificate", "cost": 0.1, "tokens_k": 1.0},
        ]
        sub = rs.build_submission("m", rows, budget_cap=20.0, library_commit="c")
        # distinct rules with a bug = {r1, r2} = 2 (not 3 certificates)
        assert sub["bugs_found"] == 2

    def test_rules_tested_excludes_skipped_budget(self):
        rows = [
            {"rule": "r1", "result": "no_certificate", "cost": 0.1, "tokens_k": 1.0},
            {"rule": "r2", "result": "skipped_budget", "cost": 0.0, "tokens_k": 0.0},
        ]
        sub = rs.build_submission("m", rows, budget_cap=20.0, library_commit="c")
        assert sub["rules_tested"] == 1


# ── run() end-to-end with FakeRunner ──────────────────────────────────────────

class TestRunFake:
    def test_produces_rankable_submission(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a", "b", "c"])
        sub = rs.run("fake/model", str(repo), budget=10.0, per_rule_budget=1.0,
                     fake=True, library_commit="deadbeef")
        assert sub["schema_version"]
        assert sub["budget_cap"] == 10.0
        assert sub["rules_tested"] >= 1
        assert sub["bugs_found"] == 0  # default fake result is no_certificate

    def test_total_spend_within_budget(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a", "b", "c", "d", "e"])
        sub = rs.run("fake/model", str(repo), budget=0.05, per_rule_budget=0.02,
                     fake=True, fake_cost=0.02, safety_margin=0.0, library_commit="c")
        assert sub["total_cost_usd"] <= 0.05 + 1e-9

    def test_safety_margin_held_back(self, tmp_path):
        # Effective cap = budget - margin; spend stays under it (the reported cap is unchanged).
        repo = _fake_repo(tmp_path, ["a", "b", "c", "d", "e", "f"])
        sub = rs.run("fake/model", str(repo), budget=0.10, per_rule_budget=0.01,
                     fake=True, fake_cost=0.01, safety_margin=0.04, library_commit="c")
        assert sub["total_cost_usd"] <= 0.06 + 1e-9
        assert sub["budget_cap"] == 0.10  # the headline cap is still the full budget

    def test_bug_results_counted(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a", "b"])
        sub = rs.run("fake/model", str(repo), budget=10.0, per_rule_budget=1.0,
                     fake=True, fake_result="bug_found", library_commit="c")
        assert sub["bugs_found"] == 2

    def test_max_rules_caps_attempts(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a", "b", "c", "d"])
        sub = rs.run("fake/model", str(repo), budget=10.0, per_rule_budget=1.0,
                     fake=True, max_rules=2, library_commit="c")
        assert len(sub["results"]) == 2

    def test_writes_output_file(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a"])
        out = tmp_path / "out" / "submission.json"
        sub = rs.run("fake/model", str(repo), budget=10.0, per_rule_budget=1.0,
                     fake=True, library_commit="c", output=out)
        assert out.exists()
        on_disk = json.loads(out.read_text())
        assert on_disk["model"] == sub["model"]


# ── schema validity ───────────────────────────────────────────────────────────

class TestSchemaValidity:
    def test_submission_matches_schema_required_fields(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a", "b"])
        sub = rs.run("fake/model", str(repo), budget=20.0, per_rule_budget=1.0,
                     fake=True, library_commit="c")
        schema = json.loads(
            (Path(rs.__file__).parent / "submission.schema.json").read_text())
        for field in schema["required"]:
            assert field in sub, f"missing required field: {field}"
