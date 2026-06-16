"""
Tests for benchmark/scheduler.py and benchmark/runner.py

Design principle: all tests use FakeRunner — no API calls, no pred calls.
Three verification scenarios from issue #4:
  1. Swappable: pipeline runs end-to-end with FakeRunner (no mini-swe imports)
  2. Resumable: kill partway, resume → only remaining sessions run, no duplicates
  3. Budget-fair: total spend ≤ cap, excess sessions marked "skipped_budget"

Test categories:
  A. FakeRunner — basic interface contract
  B. Scheduler — swappable (end-to-end with FakeRunner)
  C. Scheduler — resumable (checkpoint load + skip finished)
  D. Scheduler — budget-fair (total + per-model caps enforced)
  E. Scheduler — results files written correctly
  F. _per_model_cap helper
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from benchmark.runner import FakeRunner, AgentRunner
from benchmark.scheduler import Scheduler, _per_model_cap


# ── shared fixtures ───────────────────────────────────────────────────────────

MODELS = ["fake/model-a", "fake/model-b"]
RULES  = ["rule1", "rule2", "rule3"]


def _fake_ctx(commit: str = "abc1234" * 5 + "ab") -> MagicMock:
    ctx = MagicMock()
    ctx.commit_hash = commit[:40]
    return ctx


def _make_scheduler(
    tmp_path: Path,
    *,
    runner: FakeRunner | None = None,
    models: list[str] = MODELS,
    rules: list[str] = RULES,
    total_budget: float = 10.0,
    per_rule_budget: float = 1.0,
    resume: bool = False,
    parallelism: int = 1,
) -> Scheduler:
    if runner is None:
        runner = FakeRunner(cost_per_rule=0.01)
    return Scheduler(
        runner=runner,
        models=models,
        rules=rules,
        total_budget=total_budget,
        per_rule_budget=per_rule_budget,
        results_dir=tmp_path / "results",
        checkpoint_path=tmp_path / "checkpoint.json",
        ctx=_fake_ctx(),
        resume=resume,
        parallelism=parallelism,
    )


# ── A. FakeRunner ─────────────────────────────────────────────────────────────

class TestFakeRunner:
    def test_returns_required_fields(self):
        r = FakeRunner().run(None, "fake/model", "rule1", 1.0)
        assert "rule" in r
        assert "result" in r
        assert "cost" in r
        assert "tokens_k" in r

    def test_logs_calls(self):
        runner = FakeRunner()
        runner.run(None, "model-a", "rule1", 1.0)
        runner.run(None, "model-b", "rule2", 1.0)
        assert runner.call_log == [("model-a", "rule1"), ("model-b", "rule2")]

    def test_is_agent_runner_subclass(self):
        assert isinstance(FakeRunner(), AgentRunner)

    def test_custom_result(self):
        r = FakeRunner(result="bug_found").run(None, "m", "r", 1.0)
        assert r["result"] == "bug_found"

    def test_custom_cost(self):
        r = FakeRunner(cost_per_rule=0.05).run(None, "m", "r", 1.0)
        assert r["cost"] == pytest.approx(0.05)


# ── B. Swappable ──────────────────────────────────────────────────────────────

class TestSchedulerSwappable:
    def test_runs_end_to_end_with_fake_runner(self, tmp_path):
        runner = FakeRunner(cost_per_rule=0.01)
        s = _make_scheduler(tmp_path, runner=runner)
        results = s.run_all()
        # All models × rules should have a result
        for model in MODELS:
            assert len(results[model]) == len(RULES)

    def test_fake_runner_called_for_every_rule(self, tmp_path):
        runner = FakeRunner(cost_per_rule=0.01)
        s = _make_scheduler(tmp_path, runner=runner)
        s.run_all()
        calls = {(m, r) for m, r in runner.call_log}
        for model in MODELS:
            for rule in RULES:
                assert (model, rule) in calls

    def test_no_mini_swe_import_needed(self, tmp_path):
        """Scheduler must not import mini-swe-agent at module level."""
        import benchmark.scheduler as sched_mod
        src = Path(sched_mod.__file__).read_text(encoding="utf-8")
        assert "minisweagent" not in src
        assert "from benchmark.run_mini" not in src

    def test_results_contain_rule_names(self, tmp_path):
        s = _make_scheduler(tmp_path)
        results = s.run_all()
        for model in MODELS:
            returned_rules = {r["rule"] for r in results[model]}
            assert returned_rules == set(RULES)


# ── C. Resumable ──────────────────────────────────────────────────────────────

class TestSchedulerResumable:
    def _seed_checkpoint(self, checkpoint_path: Path, model: str, done_rules: list[str]):
        completed = {m: [] for m in MODELS}
        for rule in done_rules:
            completed[model].append({"rule": rule, "result": "no_certificate", "cost": 0.01, "tokens_k": 0.5})
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps({"completed": completed}), encoding="utf-8")

    def test_resume_skips_finished_rules(self, tmp_path):
        checkpoint = tmp_path / "checkpoint.json"
        # Pre-seed: model-a already did rule1
        self._seed_checkpoint(checkpoint, MODELS[0], ["rule1"])

        runner = FakeRunner(cost_per_rule=0.01)
        s = _make_scheduler(tmp_path, runner=runner, resume=True)
        s.run_all()

        # model-a should not have called rule1 again
        model_a_calls = [r for m, r in runner.call_log if m == MODELS[0]]
        assert "rule1" not in model_a_calls

    def test_resume_does_not_duplicate_results(self, tmp_path):
        checkpoint = tmp_path / "checkpoint.json"
        self._seed_checkpoint(checkpoint, MODELS[0], ["rule1", "rule2"])

        runner = FakeRunner(cost_per_rule=0.01)
        s = _make_scheduler(tmp_path, runner=runner, resume=True)
        results = s.run_all()

        rules_model_a = [r["rule"] for r in results[MODELS[0]]]
        # No duplicate rule entries
        assert len(rules_model_a) == len(set(rules_model_a))

    def test_resume_completes_remaining_rules(self, tmp_path):
        checkpoint = tmp_path / "checkpoint.json"
        self._seed_checkpoint(checkpoint, MODELS[0], ["rule1"])

        runner = FakeRunner(cost_per_rule=0.01)
        s = _make_scheduler(tmp_path, runner=runner, resume=True)
        results = s.run_all()

        returned = {r["rule"] for r in results[MODELS[0]]}
        assert returned == set(RULES)

    def test_no_resume_reruns_everything(self, tmp_path):
        checkpoint = tmp_path / "checkpoint.json"
        self._seed_checkpoint(checkpoint, MODELS[0], ["rule1"])

        runner = FakeRunner(cost_per_rule=0.01)
        s = _make_scheduler(tmp_path, runner=runner, resume=False)
        s.run_all()

        # Without resume, rule1 should be called again for model-a
        model_a_calls = [r for m, r in runner.call_log if m == MODELS[0]]
        assert "rule1" in model_a_calls

    def test_checkpoint_written_after_each_session(self, tmp_path):
        runner = FakeRunner(cost_per_rule=0.01)
        s = _make_scheduler(tmp_path, runner=runner, parallelism=1)
        s.run_all()
        assert (tmp_path / "checkpoint.json").exists()
        data = json.loads((tmp_path / "checkpoint.json").read_text())
        assert "completed" in data


# ── D. Budget-fair ────────────────────────────────────────────────────────────

class TestSchedulerBudgetFair:
    def test_total_spend_never_exceeds_budget(self, tmp_path):
        total_budget = 0.05  # 5 cents — very tight
        runner = FakeRunner(cost_per_rule=0.02)
        s = _make_scheduler(tmp_path, runner=runner, total_budget=total_budget, per_rule_budget=0.02)
        s.run_all()
        assert s._total_spent <= total_budget + 1e-9

    def test_excess_sessions_marked_skipped_budget(self, tmp_path):
        # Budget of 0.04 with 0.02/rule and 2 models × 3 rules = 6 sessions = $0.12 needed
        # → roughly half should be skipped
        total_budget = 0.04
        runner = FakeRunner(cost_per_rule=0.02)
        s = _make_scheduler(tmp_path, runner=runner, total_budget=total_budget, per_rule_budget=0.02)
        results = s.run_all()
        all_results = [r for rows in results.values() for r in rows]
        skipped = [r for r in all_results if r["result"] == "skipped_budget"]
        assert len(skipped) > 0

    def test_models_get_equal_budget_share(self, tmp_path):
        """Each model's spend should not exceed its equal share of the total budget."""
        total_budget = 0.06  # $0.03 per model
        runner = FakeRunner(cost_per_rule=0.01)
        s = _make_scheduler(tmp_path, runner=runner, total_budget=total_budget, per_rule_budget=0.01)
        s.run_all()
        cap_per_model = _per_model_cap(total_budget, len(MODELS))
        for model in MODELS:
            assert s._spent[model] <= cap_per_model + 1e-9

    def test_skipped_sessions_cost_zero(self, tmp_path):
        total_budget = 0.01  # almost nothing
        runner = FakeRunner(cost_per_rule=0.02)
        s = _make_scheduler(tmp_path, runner=runner, total_budget=total_budget, per_rule_budget=0.02)
        results = s.run_all()
        for rows in results.values():
            for r in rows:
                if r["result"] == "skipped_budget":
                    assert r["cost"] == 0.0


# ── E. Results files ──────────────────────────────────────────────────────────

class TestSchedulerResultsFiles:
    def test_results_file_written_per_model(self, tmp_path):
        s = _make_scheduler(tmp_path)
        s.run_all()
        for model in MODELS:
            safe = model.replace("/", "_").replace(":", "_")
            assert (tmp_path / "results" / f"{safe}.json").exists()

    def test_results_file_has_required_fields(self, tmp_path):
        s = _make_scheduler(tmp_path)
        s.run_all()
        required = {
            "model", "library_commit", "bugs_found", "total_cost_usd",
            "total_tokens_k", "efficiency_bugs_per_ktok",
            "efficiency_bugs_per_dollar", "rules_tested", "results",
        }
        for model in MODELS:
            safe = model.replace("/", "_").replace(":", "_")
            data = json.loads((tmp_path / "results" / f"{safe}.json").read_text())
            assert required <= data.keys()

    def test_results_file_rules_tested_count(self, tmp_path):
        s = _make_scheduler(tmp_path)
        results = s.run_all()
        for model in MODELS:
            safe = model.replace("/", "_").replace(":", "_")
            data = json.loads((tmp_path / "results" / f"{safe}.json").read_text())
            assert data["rules_tested"] == len(results[model])

    def test_bug_found_count_in_file(self, tmp_path):
        runner = FakeRunner(cost_per_rule=0.01, result="bug_found")
        s = _make_scheduler(tmp_path, runner=runner)
        s.run_all()
        for model in MODELS:
            safe = model.replace("/", "_").replace(":", "_")
            data = json.loads((tmp_path / "results" / f"{safe}.json").read_text())
            assert data["bugs_found"] == len(RULES)


# ── F. _per_model_cap ─────────────────────────────────────────────────────────

class TestPerModelCap:
    def test_equal_split(self):
        assert _per_model_cap(1.0, 4) == pytest.approx(0.25)

    def test_single_model(self):
        assert _per_model_cap(10.0, 1) == pytest.approx(10.0)

    def test_zero_models_no_division_error(self):
        # guard against zero-division
        assert _per_model_cap(10.0, 0) == pytest.approx(10.0)
