"""
Structural checks for the two root docs: README.md (overview) and CONTRIBUTING.md
(the single run-and-submit guide). No rendering, no external calls.
All tests are marked @pytest.mark.judgment.
"""
import pytest
from pathlib import Path

pytestmark = pytest.mark.judgment

REPO_ROOT = Path(__file__).parent.parent.parent
README = REPO_ROOT / "README.md"
GUIDE = REPO_ROOT / "CONTRIBUTING.md"


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
        assert "make run" in t or "make preflight" in t

    def test_readme_has_metrics_section(self):
        t = _text(README)
        assert "bugs/ktok" in t or "bugs_per_ktok" in t


class TestGuide:
    def test_guide_exists(self):
        assert GUIDE.exists(), "CONTRIBUTING.md (run-and-submit guide) missing from repo root"

    def test_guide_has_certificate_format(self):
        t = _text(GUIDE)
        assert "source" in t and "bundle" in t and "violation" in t

    def test_guide_has_submit_flow(self):
        # The CLI upload flow: `benchmark.submit` → private store → aggregate on Pages.
        t = _text(GUIDE)
        assert "benchmark.submit" in t and "github pages" in t
