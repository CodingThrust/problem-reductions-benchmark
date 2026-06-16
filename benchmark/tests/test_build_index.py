"""
Tests for benchmark/build_index.py and benchmark/validate_results.py

Design principle: these tests use a real temporary directory with real JSON files,
so they test actual file I/O behaviour — not mocked logic.

Test categories:
  A. _validate(): field-level schema enforcement
  B. build_index(): normal path, sorting, bug_certificates extraction, index.json skip
  C. build_index(): graceful skipping of malformed files
  D. Integration: build_index output is valid JSON and correctly structured
"""

import json
import pytest
from pathlib import Path

from benchmark.build_index import build_index, _validate
from benchmark.tests.conftest import make_results_dict


# ── A. _validate() ────────────────────────────────────────────────────────────

class TestValidate:
    def test_valid_dict_returns_none(self):
        data = make_results_dict()
        assert _validate(data, None, "test.json") is None

    @pytest.mark.parametrize("missing_field", [
        "model", "library_commit", "bugs_found", "total_cost_usd",
        "total_tokens_k", "efficiency_bugs_per_ktok", "efficiency_bugs_per_dollar",
        "rules_tested", "results",
    ])
    def test_missing_top_level_field(self, missing_field):
        data = make_results_dict()
        del data[missing_field]
        err = _validate(data, None, "test.json")
        assert err is not None
        assert missing_field in err

    def test_results_not_list(self):
        data = make_results_dict(results="not-a-list")
        err = _validate(data, None, "test.json")
        assert err is not None
        assert "array" in err

    @pytest.mark.parametrize("missing_field", ["rule", "result", "cost", "tokens_k"])
    def test_missing_per_result_field(self, missing_field):
        result_row = {"rule": "r", "result": "no_bug", "cost": 0.1, "tokens_k": 10.0}
        del result_row[missing_field]
        data = make_results_dict(results=[result_row])
        err = _validate(data, None, "test.json")
        assert err is not None
        assert missing_field in err

    def test_empty_results_list_is_valid(self):
        data = make_results_dict(results=[])
        assert _validate(data, None, "test.json") is None


# ── B. build_index(): normal path ─────────────────────────────────────────────

class TestBuildIndexNormal:
    def test_single_file_produces_one_entry(self, tmp_path):
        data = make_results_dict(
            model="anthropic/claude-sonnet-4-6",
            bugs_found=2,
            efficiency_bugs_per_ktok=0.05,
        )
        (tmp_path / "run1.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        assert len(entries) == 1
        assert entries[0]["model"] == "anthropic/claude-sonnet-4-6"
        assert entries[0]["bugs_found"] == 2

    def test_index_json_is_skipped(self, tmp_path):
        """index.json must never be treated as a results file."""
        data = make_results_dict()
        (tmp_path / "run1.json").write_text(json.dumps(data), encoding="utf-8")
        (tmp_path / "index.json").write_text(json.dumps([{"existing": "index"}]), encoding="utf-8")
        entries = build_index(tmp_path)
        assert len(entries) == 1  # index.json not counted

    def test_sorted_by_bugs_per_ktok_descending(self, tmp_path):
        """Higher efficiency must appear first."""
        efficient = make_results_dict(
            model="model-A", efficiency_bugs_per_ktok=0.10, bugs_found=5
        )
        slow = make_results_dict(
            model="model-B", efficiency_bugs_per_ktok=0.02, bugs_found=1
        )
        (tmp_path / "a.json").write_text(json.dumps(efficient), encoding="utf-8")
        (tmp_path / "b.json").write_text(json.dumps(slow), encoding="utf-8")
        entries = build_index(tmp_path)
        assert entries[0]["model"] == "model-A"
        assert entries[1]["model"] == "model-B"

    def test_library_commit_truncated_to_7(self, tmp_path):
        data = make_results_dict(library_commit="aa2d1a10cffa434871d12a4d6f411147fb7e08a8")
        (tmp_path / "run.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        assert entries[0]["library_commit"] == "aa2d1a1"

    def test_results_file_name_included(self, tmp_path):
        data = make_results_dict()
        (tmp_path / "claude-demo.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        assert entries[0]["results_file"] == "claude-demo.json"


# ── C. build_index(): bug_certificates extraction ─────────────────────────────

class TestBugCertificates:
    def _cert_result(self, rule="r1", violation="unsound_extraction", note="test") -> dict:
        return {
            "rule": rule,
            "result": "bug_found",
            "cost": 0.1,
            "tokens_k": 10.0,
            "certificate": {
                "rule": rule,
                "violation": violation,
                "source": {"type": "MaximumIndependentSet"},
                "bundle": {"target": {"type": "MaximumClique"}},
                "note": note,
            },
        }

    def test_bug_certificates_extracted(self, tmp_path):
        data = make_results_dict(
            bugs_found=1,
            results=[self._cert_result("rule_x", "unsound_extraction", "a note")],
        )
        (tmp_path / "run.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        certs = entries[0]["bug_certificates"]
        assert len(certs) == 1
        assert certs[0]["rule"] == "rule_x"
        assert certs[0]["violation"] == "unsound_extraction"
        assert certs[0]["note"] == "a note"
        assert certs[0]["source_type"] == "MaximumIndependentSet"
        assert certs[0]["target_type"] == "MaximumClique"

    def test_only_bug_found_results_included_in_certs(self, tmp_path):
        """no_certificate and rejected rows must not appear in bug_certificates."""
        results = [
            self._cert_result("bug_rule"),
            {"rule": "miss_rule", "result": "no_certificate", "cost": 0.1, "tokens_k": 5.0},
            {"rule": "rej_rule", "result": "rejected", "cost": 0.1, "tokens_k": 5.0},
        ]
        data = make_results_dict(bugs_found=1, results=results)
        (tmp_path / "run.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        certs = entries[0]["bug_certificates"]
        assert len(certs) == 1
        assert certs[0]["rule"] == "bug_rule"

    def test_empty_certs_when_no_bugs(self, tmp_path):
        data = make_results_dict(bugs_found=0)
        (tmp_path / "run.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        assert entries[0]["bug_certificates"] == []


# ── D. build_index(): error handling ──────────────────────────────────────────

class TestBuildIndexErrors:
    def test_invalid_json_file_skipped(self, tmp_path, capsys):
        (tmp_path / "bad.json").write_text("not valid json {{{", encoding="utf-8")
        entries = build_index(tmp_path)
        assert entries == []
        captured = capsys.readouterr()
        assert "bad.json" in captured.err

    def test_empty_directory_returns_empty_list(self, tmp_path):
        entries = build_index(tmp_path)
        assert entries == []

    def test_schema_violation_skipped_when_schema_provided(self, tmp_path, capsys):
        """File missing required fields is skipped when schema_path is provided."""
        from pathlib import Path as _Path
        schema_path = _Path(__file__).parent.parent / "results.schema.json"
        bad = {"model": "x"}  # missing many required fields
        (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
        entries = build_index(tmp_path, schema_path=schema_path)
        assert entries == []

    def test_valid_file_alongside_invalid_still_included(self, tmp_path):
        """A malformed file must not prevent valid files from being included."""
        (tmp_path / "bad.json").write_text("{invalid}", encoding="utf-8")
        good = make_results_dict(model="good-model")
        (tmp_path / "good.json").write_text(json.dumps(good), encoding="utf-8")
        entries = build_index(tmp_path)
        assert len(entries) == 1
        assert entries[0]["model"] == "good-model"
