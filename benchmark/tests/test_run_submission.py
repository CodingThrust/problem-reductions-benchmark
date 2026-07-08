"""
Tests for benchmark/run_submission.py — the dockerized runner entry point.

All tests run in FAKE mode (FakeRunner): no model API, no pred binary. They prove the
runner assembles a schema-valid, rankable submission.json.
"""
import json
from pathlib import Path

from benchmark import run_submission as rs
from benchmark.usage import Usage


# ── token totals are derived from the 4-bucket usage ──────────────────────────

class TestTokenTotals:
    def test_per_rule_tokens_derived_from_row_usage(self):
        rows = [
            {"rule": "r1", "result": "no_certificate", "tokens_k": 0.0,
             "usage": {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0}},
            {"rule": "r2", "result": "no_certificate", "tokens_k": 0.0,
             "usage": {"input": 0, "output": 1_000_000, "cache_read": 0, "cache_write": 0}},
        ]
        sub = rs.build_submission("m", rows, library_commit="c")
        assert sub["total_tokens_k"] == 2000.0
        assert sub["usage_totals"] == {"input": 1_000_000, "output": 1_000_000,
                                       "cache_read": 0, "cache_write": 0}

    def test_whole_repo_tokens_derived_from_session_usage(self):
        usage = Usage(input_tokens=2_000_000, output_tokens=0)
        rows = [{"rule": "r1", "result": "bug_found", "tokens_k": 0.0,
                 "certificate": {"rule": "r1"}}]
        sub = rs.build_submission("m", rows, library_commit="c", usage_totals=usage)
        assert sub["total_tokens_k"] == 2000.0
        assert sub["usage_totals"]["input"] == 2_000_000

    def test_no_usage_falls_back_to_row_tokens(self):
        rows = [{"rule": "r1", "result": "no_certificate", "tokens_k": 2.0}]
        sub = rs.build_submission("m", rows, library_commit="c")
        assert sub["total_tokens_k"] == 2.0             # row-sum fallback


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
        rows = [{"rule": "r1", "result": "no_certificate", "tokens_k": 2.0}]
        sub = rs.build_submission("anthropic/x", rows, library_commit="abc123")
        for k in ("schema_version", "model", "library_commit",
                  "bugs_found", "total_tokens_k", "rules_tested",
                  "results", "efficiency_bugs_per_ktok"):
            assert k in sub
        assert sub["model"] == "anthropic/x"
        assert sub["library_commit"] == "abc123"

    def test_bugs_counted_distinct_rules(self):
        rows = [
            {"rule": "r1", "result": "bug_found", "tokens_k": 1.0,
             "certificate": {"rule": "r1", "violation": "solve_mismatch"}},
            {"rule": "r1", "result": "bug_found", "tokens_k": 1.0,
             "certificate": {"rule": "r1", "violation": "unsound_extraction"}},
            {"rule": "r2", "result": "bug_found", "tokens_k": 1.0,
             "certificate": {"rule": "r2", "violation": "solve_mismatch"}},
            {"rule": "r3", "result": "no_certificate", "tokens_k": 1.0},
        ]
        sub = rs.build_submission("m", rows, library_commit="c")
        # distinct rules with a bug = {r1, r2} = 2 (not 3 certificates)
        assert sub["bugs_found"] == 2

    def test_rules_tested_counts_distinct_rules(self):
        rows = [
            {"rule": "r1", "result": "no_certificate", "tokens_k": 1.0},
            {"rule": "r1", "result": "bug_found", "tokens_k": 1.0,
             "certificate": {"rule": "r1"}},
            {"rule": "r2", "result": "no_certificate", "tokens_k": 1.0},
        ]
        sub = rs.build_submission("m", rows, library_commit="c")
        assert sub["rules_tested"] == 2


# ── run() end-to-end with FakeRunner ──────────────────────────────────────────

class TestRunFake:
    def test_produces_rankable_submission(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a", "b", "c"])
        sub = rs.run("fake/model", str(repo), fake=True, library_commit="deadbeef")
        assert sub["schema_version"]
        assert sub["rules_tested"] == 3
        assert sub["bugs_found"] == 0  # default fake result is no_certificate

    def test_bug_results_counted(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a", "b"])
        sub = rs.run("fake/model", str(repo), fake=True, fake_result="bug_found",
                     library_commit="c")
        assert sub["bugs_found"] == 2

    def test_max_rules_caps_attempts(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a", "b", "c", "d"])
        sub = rs.run("fake/model", str(repo), fake=True, max_rules=2, library_commit="c")
        assert len(sub["results"]) == 2

    def test_writes_output_file(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a"])
        out = tmp_path / "out" / "submission.json"
        sub = rs.run("fake/model", str(repo), fake=True, library_commit="c", output=out)
        assert out.exists()
        on_disk = json.loads(out.read_text())
        assert on_disk["model"] == sub["model"]


# ── schema validity ───────────────────────────────────────────────────────────

class TestSchemaValidity:
    def test_submission_matches_schema_required_fields(self, tmp_path):
        repo = _fake_repo(tmp_path, ["a", "b"])
        sub = rs.run("fake/model", str(repo), fake=True, library_commit="c")
        schema = json.loads(
            (Path(rs.__file__).parent / "submission.schema.json").read_text())
        for field in schema["required"]:
            assert field in sub, f"missing required field: {field}"
