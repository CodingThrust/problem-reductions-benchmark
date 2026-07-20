"""Tests for the `prb submit` client. No real network."""
import json
from pathlib import Path

import pytest

from benchmark import submit as sub
from benchmark.tests.test_top50_submission import _artifact


def _valid(tmp_path: Path, accepted_positions=()) -> Path:
    path = tmp_path / "submission.json"
    path.write_text(json.dumps(_artifact(accepted_positions=accepted_positions)),
                    encoding="utf-8")
    return path


class TestValidate:
    def test_clean_passes(self, tmp_path):
        assert sub.validate_submission(sub.load_submission(_valid(tmp_path))) == []

    def test_missing_current_protocol_field(self):
        problems = sub.validate_submission({"model": "m"})
        assert any("episodes" in problem for problem in problems)

    def test_run_error_is_rejected(self):
        artifact = _artifact(accepted_positions=())
        artifact.update(status="run_error", run_error="provider failed", rankable=False)
        assert any("incomplete" in problem for problem in sub.validate_submission(artifact))


class TestSubmit:
    def test_dry_run_does_not_send(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sub, "_post", lambda *a, **k: pytest.fail("must not POST"))
        out = sub.submit(_valid(tmp_path, accepted_positions=(1,)),
                         "https://x/submit", dry_run=True)
        assert out["claimed_bugs"] == 1 and "dry-run" in out["status"]

    def test_invalid_submission_raises_before_network(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sub, "_post", lambda *a, **k: pytest.fail("must not POST"))
        path = _valid(tmp_path)
        artifact = json.loads(path.read_text())
        artifact["episodes"].pop()
        path.write_text(json.dumps(artifact))
        with pytest.raises(ValueError, match="exactly 50"):
            sub.submit(path, "https://x/submit")

    def test_success_returns_body(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sub, "_post",
                            lambda url, payload, headers, timeout=60.0: (
                                201, {"submission_id": "abc", "status": "accepted"}))
        out = sub.submit(_valid(tmp_path), "https://x/submit", access_token="access-jwt")
        assert out["submission_id"] == "abc"

    def test_access_token_uses_access_header(self, tmp_path, monkeypatch):
        def post(url, payload, headers, timeout=60.0):
            assert headers == {"Cf-Access-Token": "access-jwt"}
            return 201, {"submission_id": "abc", "status": "accepted"}

        monkeypatch.setattr(sub, "_post", post)
        out = sub.submit(_valid(tmp_path), "https://x/submit", access_token="access-jwt")
        assert out["submission_id"] == "abc"

    def test_non_2xx_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sub, "_post", lambda *a, **k: (429, {"error": "quota exceeded"}))
        with pytest.raises(ValueError, match="429.*quota"):
            sub.submit(_valid(tmp_path), "https://x/submit", access_token="access-jwt")

    def test_missing_url_or_key_raises(self, tmp_path):
        with pytest.raises(ValueError, match="URL"):
            sub.submit(_valid(tmp_path), "", access_token="access-jwt")
        with pytest.raises(ValueError, match="intake credential"):
            sub.submit(_valid(tmp_path), "https://x/submit")

    def test_non_json_success_is_not_reported_as_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sub, "_post", lambda *a, **k: (200, "Access login page"))
        with pytest.raises(ValueError, match="non-JSON.*Access login"):
            sub.submit(_valid(tmp_path), "https://x/submit", access_token="access-jwt")

    def test_success_without_submission_id_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sub, "_post", lambda *a, **k: (201, {"status": "accepted"}))
        with pytest.raises(ValueError, match="no submission_id"):
            sub.submit(_valid(tmp_path), "https://x/submit", access_token="access-jwt")
