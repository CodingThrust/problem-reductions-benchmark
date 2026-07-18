"""Tests for benchmark/submit.py — the `prb submit` CLI client. No real network."""
import json
from pathlib import Path

import pytest

from benchmark import submit as sub


def _valid(tmp_path: Path, results=None) -> Path:
    doc = {
        "model": "anthropic/test", "library_commit": "deadbeef",
        "bugs_found": 0,
        "total_tokens_k": 10.0,
        "rules_tested": 1, "results": results if results is not None else [
            {"rule": "r1", "result": "no_certificate", "tokens_k": 1.0}],
        "submit_limit": 100,
        "submit_log": [],
    }
    p = tmp_path / "submission.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _bug_row(with_cert=True, with_traj=True) -> dict:
    row = {"rule": "r1", "result": "bug_found", "tokens_k": 1.0}
    if with_cert:
        row["certificate"] = {"rule": "r1", "source": {}}
    if with_traj:
        row["trajectory"] = [{"role": "assistant", "content": "CERTIFICATE_START\n{}\nCERTIFICATE_END"}]
    return row


class TestValidate:
    def test_clean_passes(self, tmp_path):
        assert sub.validate_submission(sub.load_submission(_valid(tmp_path))) == []

    def test_missing_envelope_field(self):
        assert any("model" in p for p in sub.validate_submission({"results": []}))

    def test_bug_found_needs_certificate(self):
        doc = {**json.loads('{"model":"m","library_commit":"c",'
                            '"total_tokens_k":0,"rules_tested":1,'
                            '"submit_limit":100,"submit_log":[]}'),
               "results": [_bug_row(with_cert=False, with_traj=False)]}
        problems = sub.validate_submission(doc)
        assert any("no certificate" in p for p in problems)

    def test_bug_found_complete_passes(self, tmp_path):
        p = _valid(tmp_path, results=[_bug_row()])
        assert sub.validate_submission(sub.load_submission(p)) == []

    def test_submit_ledger_counters_must_match(self, tmp_path):
        doc = sub.load_submission(_valid(tmp_path))
        doc["submit_limit"] = 0
        doc["submit_log"] = [{"attempt": 1, "accepted": False, "reason": "bad"}]
        problems = sub.validate_submission(doc)
        assert any("exceeds submit_limit" in p for p in problems)

    def test_valid_submit_ledger_passes(self, tmp_path):
        doc = sub.load_submission(_valid(tmp_path))
        doc["submit_limit"] = 3
        doc["submit_log"] = [{"attempt": 1, "accepted": False, "reason": "bad"}]
        assert sub.validate_submission(doc) == []

    def test_schema_version_is_not_part_of_current_submission(self, tmp_path):
        doc = sub.load_submission(_valid(tmp_path))
        assert "schema_version" not in doc
        assert sub.validate_submission(doc) == []


class TestSubmit:
    def test_dry_run_does_not_send(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sub, "_post", lambda *a, **k: pytest.fail("must not POST on dry-run"))
        out = sub.submit(_valid(tmp_path, results=[_bug_row()]), "https://x/submit", "k", dry_run=True)
        assert out["claimed_bugs"] == 1 and "dry-run" in out["status"]

    def test_invalid_submission_raises_before_network(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sub, "_post", lambda *a, **k: pytest.fail("must not POST invalid"))
        p = _valid(tmp_path, results=[_bug_row(with_cert=False, with_traj=False)])
        with pytest.raises(ValueError, match="certificate"):
            sub.submit(p, "https://x/submit", "k")

    def test_success_returns_body(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sub, "_post",
                            lambda url, key, payload, timeout=60.0: (201, {"submission_id": "abc", "status": "accepted"}))
        out = sub.submit(_valid(tmp_path), "https://x/submit", "k")
        assert out["submission_id"] == "abc"

    def test_non_2xx_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sub, "_post",
                            lambda *a, **k: (429, {"error": "quota exceeded"}))
        with pytest.raises(ValueError, match="429.*quota"):
            sub.submit(_valid(tmp_path), "https://x/submit", "k")

    def test_missing_url_or_key_raises(self, tmp_path):
        with pytest.raises(ValueError, match="URL"):
            sub.submit(_valid(tmp_path), "", "k")
        with pytest.raises(ValueError, match="API key"):
            sub.submit(_valid(tmp_path), "https://x/submit", "")
