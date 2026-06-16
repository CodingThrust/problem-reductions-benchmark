"""
Tests for issue #7: CI workflow file structure.

Parses .github/workflows/publish.yml as YAML and verifies the schema-gated
publish pipeline is correctly configured. No GitHub Actions execution needed.

All tests are marked @pytest.mark.judgment.
"""
import pytest

pytestmark = pytest.mark.judgment

WORKFLOW_PATH = (
    __import__("pathlib").Path(__file__).parent.parent.parent
    / ".github" / "workflows" / "publish.yml"
)


def _workflow() -> dict:
    import yaml
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


# ── 1. File exists ────────────────────────────────────────────────────────────

class TestWorkflowExists:
    def test_workflow_file_exists(self):
        assert WORKFLOW_PATH.exists(), f"Missing: {WORKFLOW_PATH}"


def _on(w: dict) -> dict:
    """YAML parses bare 'on' as boolean True — handle both."""
    return w.get("on") or w.get(True) or {}


# ── 2. Trigger configuration ──────────────────────────────────────────────────

class TestWorkflowTrigger:
    def test_workflow_triggers_on_push(self):
        w = _workflow()
        assert "push" in _on(w), "Workflow must trigger on push"

    def test_workflow_has_path_filter(self):
        """Push trigger must have a paths filter covering results/*.json."""
        w = _workflow()
        push = _on(w).get("push", {})
        paths = push.get("paths", [])
        assert any("results" in p for p in paths), \
            f"Push trigger must filter on results/**; got paths={paths}"


# ── 3. Job structure ──────────────────────────────────────────────────────────

class TestWorkflowJobs:
    def _steps(self, w: dict, job_key: str) -> list[dict]:
        return w.get("jobs", {}).get(job_key, {}).get("steps", [])

    def _all_step_text(self, steps: list[dict]) -> str:
        parts = []
        for s in steps:
            parts.append(str(s.get("run", "")))
            parts.append(str(s.get("uses", "")))
            parts.append(str(s.get("name", "")))
        return " ".join(parts)

    def test_validate_step_before_build_step(self):
        """validate_results must come before build_index in the same job."""
        w = _workflow()
        for job in w.get("jobs", {}).values():
            steps = job.get("steps", [])
            text_list = [
                str(s.get("run", "")) + str(s.get("name", ""))
                for s in steps
            ]
            validate_idx = next(
                (i for i, t in enumerate(text_list) if "validate_results" in t or "validate-results" in t),
                None,
            )
            build_idx = next(
                (i for i, t in enumerate(text_list) if "build_index" in t or "build-index" in t),
                None,
            )
            if validate_idx is not None and build_idx is not None:
                assert validate_idx < build_idx, \
                    "validate_results step must appear before build_index step"
                return
        pytest.fail("Could not find both validate_results and build_index steps in any job")

    def test_deploy_needs_validate_job(self):
        """Deploy job must declare needs on the validate-and-build job."""
        w = _workflow()
        jobs = w.get("jobs", {})
        # Find the deploy job (by name or key containing "deploy")
        deploy_job = next(
            (v for k, v in jobs.items() if "deploy" in k.lower()),
            None,
        )
        assert deploy_job is not None, "No deploy job found"
        needs = deploy_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert len(needs) > 0, "Deploy job must declare 'needs' to gate on validate job"

    def test_workflow_deploys_to_pages(self):
        """Workflow must use GitHub Pages deployment."""
        w = _workflow()
        full_text = str(w)
        assert "pages" in full_text.lower() or "deploy-pages" in full_text.lower(), \
            "Workflow must deploy to GitHub Pages"

    def test_validate_step_uses_validate_results_module(self):
        """A step must run python -m benchmark.validate_results."""
        w = _workflow()
        full_text = str(w)
        assert "validate_results" in full_text or "validate-results" in full_text, \
            "Workflow must invoke benchmark.validate_results"

    def test_build_step_uses_build_index_module(self):
        """A step must run python -m benchmark.build_index."""
        w = _workflow()
        full_text = str(w)
        assert "build_index" in full_text or "build-index" in full_text, \
            "Workflow must invoke benchmark.build_index"
