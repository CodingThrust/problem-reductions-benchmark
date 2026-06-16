"""
Tests for issue #10: README.md and CONTRIBUTING.md existence and content.

Structural checks only — no rendering, no external calls.
All tests are marked @pytest.mark.judgment.
"""
import pytest
from pathlib import Path

pytestmark = pytest.mark.judgment

REPO_ROOT = Path(__file__).parent.parent.parent
README = REPO_ROOT / "README.md"
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


class TestReadme:
    def test_readme_exists(self):
        assert README.exists(), "README.md missing from repo root"

    def test_readme_has_what_section(self):
        t = _text(README)
        assert "violation" in t or "counterexample" in t

    def test_readme_has_how_to_add_model(self):
        t = _text(README)
        assert "agentrunner" in t or "runner" in t

    def test_readme_has_run_locally(self):
        t = _text(README)
        assert "make demo" in t or "repo_dir" in t

    def test_readme_has_metrics_section(self):
        t = _text(README)
        assert "bugs/ktok" in t or "bugs_per_ktok" in t


class TestContributing:
    def test_contributing_exists(self):
        assert CONTRIBUTING.exists(), "CONTRIBUTING.md missing from repo root"

    def test_contributing_has_certificate_schema(self):
        t = _text(CONTRIBUTING)
        assert "source" in t and "bundle" in t and "violation" in t

    def test_contributing_has_verify_calibration(self):
        t = _text(CONTRIBUTING)
        assert "verify-calibration" in t
