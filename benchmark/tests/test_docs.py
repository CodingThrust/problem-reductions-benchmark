"""
Structural checks for the two root docs: README.md (overview) and CONTRIBUTING.md
(the single run-and-submit guide). No rendering, no external calls.
All tests are marked @pytest.mark.judgment.
"""
import pytest
from pathlib import Path

from benchmark.env_setup import DEFAULT_PINNED_COMMIT, DEFAULT_PINNED_VERSION

pytestmark = pytest.mark.judgment

REPO_ROOT = Path(__file__).parent.parent.parent
README = REPO_ROOT / "README.md"
GUIDE = REPO_ROOT / "CONTRIBUTING.md"
ENV_EXAMPLE = REPO_ROOT / "submission.env.example"
API_SKILL = REPO_ROOT / ".agents/skills/run-api-benchmark/SKILL.md"
CLI_SKILL = REPO_ROOT / ".agents/skills/run-cli-benchmark/SKILL.md"


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

    def test_readme_lists_current_round_contract(self):
        text = README.read_text(encoding="utf-8")
        assert DEFAULT_PINNED_COMMIT in text
        assert f"`{DEFAULT_PINNED_VERSION}`" in text
        assert "no schema-version field" in text.lower()


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


class TestBackendRouteSeparation:
    def test_env_template_does_not_select_cli_with_agent_backend(self):
        t = _text(ENV_EXAMPLE)
        assert "agent_backend=" not in t
        assert "local_backend=codex" in t

    def test_api_skill_is_container_only(self):
        t = _text(API_SKILL)
        assert "mini-swe" in t and "runner-pull" in t
        assert "never run one inside the container" in t

    def test_cli_skill_is_host_only(self):
        t = _text(CLI_SKILL)
        assert "make run-local" in t and "local_backend" in t
        assert "do not set `agent_backend`" in t
        assert "or start docker/podman" in t

    @pytest.mark.parametrize("skill", [API_SKILL, CLI_SKILL])
    def test_each_skill_exposes_three_submission_goals(self, skill):
        t = _text(skill)
        assert "keep and validate" in t
        assert "intake test" in t
        assert "official submission" in t
