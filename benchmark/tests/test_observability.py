"""
Tests for issue #13: observability — error_count and skip_count in index entries.

build_index() must aggregate:
  - error_count: rows where result starts with "error:"
  - skip_count: rows where result == "skipped_budget"

All tests are marked @pytest.mark.judgment.
"""
import json
import pytest
from pathlib import Path

pytestmark = pytest.mark.judgment

from benchmark.build_index import build_index
from benchmark.tests.conftest import make_results_dict


def _results_with_mixed_outcomes() -> list[dict]:
    return [
        {"rule": "r1", "result": "bug_found",       "cost": 0.1, "tokens_k": 5.0},
        {"rule": "r2", "result": "no_certificate",  "cost": 0.1, "tokens_k": 5.0},
        {"rule": "r3", "result": "error: timeout",  "cost": 0.0, "tokens_k": 0.0},
        {"rule": "r4", "result": "error: api_fail", "cost": 0.0, "tokens_k": 0.0},
        {"rule": "r5", "result": "skipped_budget",  "cost": 0.0, "tokens_k": 0.0},
        {"rule": "r6", "result": "skipped_budget",  "cost": 0.0, "tokens_k": 0.0},
    ]


class TestObservabilityFields:
    def test_error_count_in_index_entry(self, tmp_path):
        data = make_results_dict(results=_results_with_mixed_outcomes(), bugs_found=1)
        (tmp_path / "run.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        assert "error_count" in entries[0], "index entry must have error_count"

    def test_skip_count_in_index_entry(self, tmp_path):
        data = make_results_dict(results=_results_with_mixed_outcomes(), bugs_found=1)
        (tmp_path / "run.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        assert "skip_count" in entries[0], "index entry must have skip_count"

    def test_error_count_correct_value(self, tmp_path):
        data = make_results_dict(results=_results_with_mixed_outcomes(), bugs_found=1)
        (tmp_path / "run.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        assert entries[0]["error_count"] == 2

    def test_skip_count_correct_value(self, tmp_path):
        data = make_results_dict(results=_results_with_mixed_outcomes(), bugs_found=1)
        (tmp_path / "run.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        assert entries[0]["skip_count"] == 2

    def test_zero_errors_zero_skips(self, tmp_path):
        results = [
            {"rule": "r1", "result": "bug_found",      "cost": 0.1, "tokens_k": 5.0},
            {"rule": "r2", "result": "no_certificate", "cost": 0.1, "tokens_k": 5.0},
        ]
        data = make_results_dict(results=results, bugs_found=1)
        (tmp_path / "run.json").write_text(json.dumps(data), encoding="utf-8")
        entries = build_index(tmp_path)
        assert entries[0]["error_count"] == 0
        assert entries[0]["skip_count"] == 0
