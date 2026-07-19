"""
Structural checks for the two root docs: README.md (overview) and CONTRIBUTING.md
(the single run-and-submit guide). No rendering, no external calls.
All tests are marked @pytest.mark.judgment.
"""
import pytest
from pathlib import Path

from benchmark.env_setup import PINNED_COMMIT, PINNED_PRED_VERSION

pytestmark = pytest.mark.judgment

REPO_ROOT = Path(__file__).parent.parent.parent
README = REPO_ROOT / "README.md"
GUIDE = REPO_ROOT / "CONTRIBUTING.md"
ENV_EXAMPLE = REPO_ROOT / "submission.env.example"
API_SKILL = REPO_ROOT / ".agents/skills/run-api-benchmark/SKILL.md"
CLI_SKILL = REPO_ROOT / ".agents/skills/run-cli-benchmark/SKILL.md"
SUBMIT_SKILL = REPO_ROOT / ".agents/skills/submit-benchmark-result/SKILL.md"
SCORER_WORKFLOW = REPO_ROOT / ".github/workflows/score-from-r2.yml"
SUBMISSIONS_README = REPO_ROOT / "submissions/README.md"
SITE_INDEX = REPO_ROOT / "site/index.html"
INTAKE_README = REPO_ROOT / "intake/cloudflare-worker/README.md"


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
        assert PINNED_COMMIT in text
        assert f"`{PINNED_PRED_VERSION}`" in text
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

    @pytest.mark.parametrize("skill", [API_SKILL, CLI_SKILL])
    def test_run_skills_delegate_upload(self, skill):
        assert "$submit-benchmark-result" in _text(skill)


class TestSubmitSkill:
    def test_submit_skill_exists(self):
        assert SUBMIT_SKILL.exists(), "submit-benchmark-result skill missing"

    def test_submit_skill_validates_before_upload(self):
        t = _text(SUBMIT_SKILL)
        assert "python3 -m benchmark.submit" in t
        assert "--dry-run" in t
        assert "explicit confirmation" in t

    def test_submit_skill_has_no_intake_test_mode(self):
        t = _text(SUBMIT_SKILL)
        assert "--test" not in t
        assert "smoke test" not in t

    def test_submit_skill_uses_access_without_github_credentials(self):
        t = _text(SUBMIT_SKILL)
        assert "cloudflared access login" in t
        assert "prb_access_token" in t
        assert 'test -n "$prb_access_token"' in t
        assert "unset prb_access_token" in t
        assert "gh auth token" in t
        assert "github pat" in t

    def test_submit_skill_prepares_cloudflared(self):
        t = _text(SUBMIT_SKILL)
        assert "command -v cloudflared" in t
        assert "cloudflared --version" in t
        assert "brew install cloudflared" in t
        assert "obtain confirmation before running an installer" in t

    def test_submit_skill_explains_access_denial(self):
        t = _text(SUBMIT_SKILL)
        assert "http 403" in t
        assert "github primary email" in t
        assert "authorization list by a maintainer" in t

    def test_submit_skill_triggers_scoring_without_reset(self):
        t = _text(SUBMIT_SKILL)
        assert "gh workflow run score-from-r2.yml" in t
        assert "--repo codingthrust/problem-reductions-benchmark" in t
        assert "-f reset_results=false" in t
        assert "never set `reset_results=true`" in t
        assert "actions write permission" in t

    @pytest.mark.parametrize(
        "path", [GUIDE, SUBMIT_SKILL, SUBMISSIONS_README, SITE_INDEX, INTAKE_README])
    def test_public_submission_docs_have_no_shared_intake_key(self, path):
        assert "PRB_API_KEY" not in path.read_text(encoding="utf-8")


class TestScorerWorkflow:
    def test_empty_r2_queue_is_valid(self):
        text = SCORER_WORKFLOW.read_text(encoding="utf-8")
        assert "jq -r '.[]? | select(endswith(\".json\"))'" in text
