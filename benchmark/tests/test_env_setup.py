"""
Tests for benchmark/env_setup.py

Design principle: these tests must not depend on the real filesystem state.
We use pytest's tmp_path and monkeypatch to isolate each failure mode.
The happy path test DOES require the real repo + pred binary — it's marked
with a custom marker so CI can skip it on machines without the library.

Test categories:
  A. find_pred_binary(): pred not in PATH → RuntimeError
  B. verify_commit(): wrong commit → ValueError; correct commit → returns hash
  C. setup_env(): missing repo path → FileNotFoundError
  D. setup_env(): wrong commit → ValueError (integration of A+B+C)
  E. setup_env(): happy path (requires real repo + pred, marked as integration)
"""

import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from benchmark.env_setup import (
    PINNED_COMMIT,
    PINNED_PRED_VERSION,
    find_pred_binary,
    pred_version,
    setup_env,
    verify_commit,
    verify_pred_version,
)


# ── pred version pin (mock subprocess so no real pred needed) ──────────────────

class TestPredVersion:
    def _fake_run(self, stdout):
        return lambda *a, **k: MagicMock(stdout=stdout, returncode=0)

    def test_parses_version(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", self._fake_run("pred 0.6.0\n"))
        assert pred_version("pred") == "0.6.0"

    def test_matching_version_ok(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", self._fake_run(f"pred {PINNED_PRED_VERSION}\n"))
        assert verify_pred_version("pred") == PINNED_PRED_VERSION

    def test_mismatch_raises(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", self._fake_run("pred 0.5.0\n"))
        with pytest.raises(ValueError, match="0.5.0"):
            verify_pred_version("pred")

    def test_env_override(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", self._fake_run("pred 0.5.0\n"))
        monkeypatch.setenv("EXPECTED_PRED_VERSION", "0.5.0")
        assert verify_pred_version("pred") == "0.5.0"

    def test_empty_expected_skips_check(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", self._fake_run("pred 9.9.9\n"))
        monkeypatch.setenv("EXPECTED_PRED_VERSION", "")
        assert verify_pred_version("pred") == "9.9.9"  # returns actual, no raise

    def test_unparseable_raises(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", self._fake_run("garbage\n"))
        with pytest.raises(ValueError, match="could not parse"):
            pred_version("pred")


# ── A. find_pred_binary() ─────────────────────────────────────────────────────

class TestFindPredBinary:
    def test_pred_not_in_path_raises(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(RuntimeError, match="pred binary not found"):
            find_pred_binary()

    def test_pred_found_returns_path(self, monkeypatch, tmp_path):
        fake_pred = tmp_path / "pred"
        fake_pred.touch()
        monkeypatch.setattr("shutil.which", lambda name: str(fake_pred))
        result = find_pred_binary()
        assert result == fake_pred


# ── B. verify_commit() ───────────────────────────────────────────────────────

class TestVerifyCommit:
    def test_correct_commit_returns_hash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: MagicMock(stdout=PINNED_COMMIT + "\n", returncode=0),
        )
        result = verify_commit(tmp_path, PINNED_COMMIT)
        assert result == PINNED_COMMIT

    def test_wrong_commit_raises_value_error(self, tmp_path, monkeypatch):
        wrong = "deadbeef" * 5  # 40-char wrong hash
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: MagicMock(stdout=wrong + "\n", returncode=0),
        )
        with pytest.raises(ValueError, match="expected"):
            verify_commit(tmp_path, PINNED_COMMIT)

    def test_error_message_shows_short_hashes(self, tmp_path, monkeypatch):
        """Error message must show 7-char short hashes for readability."""
        wrong = "abcdef1234567890" * 3  # 48-char hash — take first 40
        wrong = wrong[:40]
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: MagicMock(stdout=wrong + "\n", returncode=0),
        )
        with pytest.raises(ValueError) as exc_info:
            verify_commit(tmp_path, PINNED_COMMIT)
        msg = str(exc_info.value)
        # Both hashes in message should be truncated to 7 chars
        assert PINNED_COMMIT[:7] in msg
        assert wrong[:7] in msg


# ── C. setup_env(): missing repo path ─────────────────────────────────────────

class TestSetupEnvMissingRepo:
    def test_nonexistent_path_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            setup_env("/path/that/definitely/does/not/exist/xyz123")

    def test_empty_string_path_raises(self):
        # "" resolves to "." which may exist; the invariant is that a non-repo
        # path always raises — we test the specific nonexistent-path case above.
        # For empty string: Path("") == Path(".") exists, so this is not guaranteed
        # to raise FileNotFoundError. Skip this variant.
        pytest.skip("empty string resolves to cwd which may be a valid repo")


# ── D. setup_env(): wrong commit integration ──────────────────────────────────

class TestSetupEnvWrongCommit:
    def test_wrong_commit_raises_value_error(self, tmp_path, monkeypatch):
        """setup_env on a real directory but wrong commit must raise ValueError."""
        # Mock find_pred_binary to not require a real pred install
        monkeypatch.setattr(
            "benchmark.env_setup.find_pred_binary",
            lambda: tmp_path / "fake_pred",
        )
        # Mock verify_commit to simulate wrong commit
        monkeypatch.setattr(
            "benchmark.env_setup.verify_commit",
            lambda path, expected: (_ for _ in ()).throw(
                ValueError(f"wrong commit: deadbeef, expected {expected[:7]}")
            ),
        )
        with pytest.raises(ValueError, match="wrong commit"):
            setup_env(tmp_path)

    def test_pred_missing_raises_runtime_error(self, tmp_path, monkeypatch):
        """setup_env when pred binary is absent must raise RuntimeError."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(RuntimeError, match="pred binary not found"):
            setup_env(tmp_path)


# ── E. Happy path (integration, requires real env) ────────────────────────────

REAL_REPO = Path("C:/Users/ASUS/Desktop/111/reduction/problem-reductions")

@pytest.mark.integration
@pytest.mark.skipif(
    not REAL_REPO.exists(),
    reason="Real problem-reductions repo not present at expected path",
)
class TestSetupEnvHappyPath:
    def test_returns_env_context_with_correct_fields(self):
        from benchmark.env_context import EnvContext
        ctx = setup_env(REAL_REPO)
        assert isinstance(ctx, EnvContext)
        assert ctx.commit_hash == PINNED_COMMIT
        assert ctx.pred_binary.exists()
        assert ctx.repo_path.exists()

    def test_commit_hash_is_full_40_chars(self):
        ctx = setup_env(REAL_REPO)
        assert len(ctx.commit_hash) == 40

    def test_repo_path_is_absolute(self):
        ctx = setup_env(REAL_REPO)
        assert ctx.repo_path.is_absolute()
