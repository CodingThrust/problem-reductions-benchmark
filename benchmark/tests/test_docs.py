"""
Structural checks for the two root docs: README.md (overview) and CONTRIBUTING.md
(the single run-and-submit guide). No rendering, no external calls.
All tests are marked @pytest.mark.judgment.
"""
import os
import subprocess
from pathlib import Path

import pytest

from benchmark.env_setup import PINNED_COMMIT, PINNED_PRED_VERSION

pytestmark = pytest.mark.judgment

REPO_ROOT = Path(__file__).parent.parent.parent
README = REPO_ROOT / "README.md"
GUIDE = REPO_ROOT / "CONTRIBUTING.md"
ENV_EXAMPLE = REPO_ROOT / "submission.env.example"
RUN_SKILL = REPO_ROOT / ".agents/skills/run-benchmark/SKILL.md"
SUBMIT_SKILL = REPO_ROOT / ".agents/skills/submit-benchmark-result/SKILL.md"
TRIGGER_SCORING = SUBMIT_SKILL.parent / "scripts/trigger-scoring.sh"
SCORER_WORKFLOW = REPO_ROOT / ".github/workflows/score-from-r2.yml"
SUBMISSIONS_README = REPO_ROOT / "submissions/README.md"
SITE_INDEX = REPO_ROOT / "site/index.html"
INTAKE_README = REPO_ROOT / "intake/cloudflare-worker/README.md"
VERSION_FILE = REPO_ROOT / "VERSION"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def _run_trigger_with_fake_gh(
        tmp_path, gh_cases,
        submission_id="d78aad10-e44a-40b9-b57c-093a7bb47330"):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh = fake_bin / "gh"
    gh.write_text(
        f'''#!/bin/sh
case "$1 $2" in
  "auth status") exit 0 ;;
  "api user") echo submitter ;;
{gh_cases}
  *) exit 1 ;;
esac
''',
        encoding="utf-8")
    gh.chmod(0o755)
    env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", str(TRIGGER_SCORING), submission_id],
        text=True, capture_output=True, env=env, check=False)


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
        assert "verified distinct-rule bugs" in t
        assert "equal bug counts are ties" in t

    def test_readme_lists_current_benchmark(self):
        text = README.read_text(encoding="utf-8")
        assert "not a user-selectable configuration" in text
        assert PINNED_COMMIT in text
        assert f"`{PINNED_PRED_VERSION}`" in text
        assert "elapsed time" in text.lower()


class TestGuide:
    def test_guide_exists(self):
        assert GUIDE.exists(), "CONTRIBUTING.md (run-and-submit guide) missing from repo root"

    def test_guide_has_certificate_format(self):
        t = _text(GUIDE)
        assert "certificate" in t and "pred" in t and "reproducible" in t

    def test_guide_has_submit_flow(self):
        # The CLI upload flow: `benchmark.submit` → private store → aggregate on Pages.
        t = _text(GUIDE)
        assert "benchmark.submit" in t and "aggregate leaderboard" in t


class TestSingleProtocol:
    def test_env_template_exposes_only_model_api_configuration(self):
        t = _text(ENV_EXAMPLE)
        assert "model api in docker" in t
        assert "whole-repository" not in t and "local_backend" not in t

    def test_run_skill_uses_the_containerized_protocol(self):
        t = _text(RUN_SKILL)
        assert "standardized" in t and "runner-pull" in t

    @pytest.mark.parametrize("skill", [RUN_SKILL])
    def test_each_skill_exposes_only_local_and_official_goals(self, skill):
        t = _text(skill)
        assert "local" in t
        assert "official" in t
        assert "intake test" not in t

    @pytest.mark.parametrize("skill", [RUN_SKILL])
    def test_run_skills_delegate_upload(self, skill):
        t = _text(skill)
        assert "$submit-benchmark-result" in t
        assert "owns upload" in t

    @pytest.mark.parametrize("skill", [RUN_SKILL])
    def test_run_skills_confirm_the_benchmark_version(self, skill):
        t = _text(skill)
        assert "make -s print-benchmark-version" in t
        assert "make -s print-pr-ref" in t
        assert "v0.6.0" not in t
        assert "problem-reductions-benchmark/blob/main/version" in t
        assert "benchmark version:" in t
        assert "wait for confirmation" in t
        assert "checkout is outdated" in t

    def test_version_has_one_source_of_truth(self):
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
        assert version.startswith("v")
        for target in ("print-benchmark-version", "print-pr-ref"):
            result = subprocess.run(
                ["make", "-s", target], cwd=REPO_ROOT,
                text=True, capture_output=True, check=True)
            assert result.stdout.strip() == version

    @pytest.mark.parametrize("workflow", [
        REPO_ROOT / ".github/workflows/ci.yml",
        REPO_ROOT / ".github/workflows/score-from-r2.yml",
        REPO_ROOT / ".github/workflows/runner-image.yml",
    ])
    def test_workflows_read_version_file(self, workflow):
        text = workflow.read_text(encoding="utf-8")
        assert "cat VERSION" in text
        assert "PR_REF: v" not in text

    def test_router_sends_existing_results_to_submit_skill(self):
        t = _text(RUN_SKILL)
        assert "already has a `submission.json`" in t
        assert "$submit-benchmark-result" in t

class TestSubmitSkill:
    def test_submit_skill_exists(self):
        assert SUBMIT_SKILL.exists(), "submit-benchmark-result skill missing"
        assert os.access(TRIGGER_SCORING, os.X_OK), "trigger-scoring.sh must be executable"

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
        assert '&& [ -n "$prb_access_token" ]' in t
        assert "submission was not uploaded" in t
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
        assert "scripts/trigger-scoring.sh" in t
        assert "reset_results" not in t
        assert "actions write permission" in t
        assert "return that pr url" in t

    def test_trigger_scoring_returns_matching_pr(self, tmp_path):
        marker = tmp_path / "pr-list-called"
        result = _run_trigger_with_fake_gh(
            tmp_path,
            f'''  "workflow run") echo https://github.com/CodingThrust/problem-reductions-benchmark/actions/runs/123 ;;
  "run watch") exit 0 ;;
  "pr list") : > "{marker}"; echo https://github.com/CodingThrust/problem-reductions-benchmark/pull/456 ;;''')
        assert result.returncode == 0
        assert result.stdout.rstrip().endswith("/pull/456")
        assert marker.exists()

    def test_trigger_scoring_stops_before_pr_lookup_when_run_fails(self, tmp_path):
        marker = tmp_path / "pr-list-called"
        result = _run_trigger_with_fake_gh(
            tmp_path,
            f'''  "workflow run") echo https://github.com/CodingThrust/problem-reductions-benchmark/actions/runs/123 ;;
  "run watch") exit 1 ;;
  "pr list") : > "{marker}"; exit 0 ;;''')
        assert result.returncode == 1
        assert "actions/runs/123" in result.stderr
        assert not marker.exists()

    def test_trigger_scoring_recovers_run_url_when_dispatch_prints_none(self, tmp_path):
        result = _run_trigger_with_fake_gh(
            tmp_path,
            '''  "workflow run") exit 0 ;;
  "run list") echo https://github.com/CodingThrust/problem-reductions-benchmark/actions/runs/789 ;;
  "run watch") exit 0 ;;
  "pr list") echo https://github.com/CodingThrust/problem-reductions-benchmark/pull/987 ;;''')
        assert result.returncode == 0
        assert result.stdout.rstrip().endswith("/pull/987")

    @pytest.mark.parametrize(
        "path", [GUIDE, SUBMIT_SKILL, SUBMISSIONS_README, SITE_INDEX, INTAKE_README])
    def test_public_submission_docs_have_no_shared_intake_key(self, path):
        assert "PRB_API_KEY" not in path.read_text(encoding="utf-8")


class TestScorerWorkflow:
    def test_empty_r2_queue_is_valid(self):
        text = SCORER_WORKFLOW.read_text(encoding="utf-8")
        assert "jq -r '.[]? | select(endswith(\".json\"))'" in text

    def test_workflow_exposes_no_result_reset(self):
        assert "reset_results" not in _text(SCORER_WORKFLOW)
