"""
Structural checks for the two root docs: README.md (overview) and SUBMISSION.md
(the single run-and-submit guide). No rendering, no external calls.
All tests are marked @pytest.mark.judgment.
"""
import pytest
from pathlib import Path

pytestmark = pytest.mark.judgment

REPO_ROOT = Path(__file__).parent.parent.parent
README = REPO_ROOT / "README.md"
SUBMISSION = REPO_ROOT / "SUBMISSION.md"


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


class TestSubmission:
    def test_submission_exists(self):
        assert SUBMISSION.exists(), "SUBMISSION.md missing from repo root"

    def test_submission_has_certificate_format(self):
        t = _text(SUBMISSION)
        assert "source" in t and "bundle" in t and "violation" in t

    def test_submission_has_github_pr_flow(self):
        t = _text(SUBMISSION)
        assert "pull request" in t and "github pages" in t
