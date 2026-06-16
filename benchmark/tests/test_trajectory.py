"""
Tests for issue #12: session trajectory archive.

run_mini.save_trajectory() and run_one() trajectory_dir parameter.
All unit tests — no API calls, no minisweagent.

All tests are marked @pytest.mark.judgment.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.judgment


def _fake_messages() -> list[dict]:
    return [
        {"role": "user", "content": "find a bug in rule_x"},
        {"role": "assistant", "content": "running pred reduce...\npred reduce src.json --to MaximumClique"},
        {"role": "tool", "content": "pred output here"},
    ]


class TestSaveTrajectory:
    def test_creates_jsonl_file(self, tmp_path):
        from benchmark.run_mini import save_trajectory
        out = tmp_path / "traj.jsonl"
        save_trajectory(_fake_messages(), out)
        assert out.exists()

    def test_one_line_per_message(self, tmp_path):
        from benchmark.run_mini import save_trajectory
        msgs = _fake_messages()
        out = tmp_path / "traj.jsonl"
        save_trajectory(msgs, out)
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == len(msgs)

    def test_each_line_is_valid_json(self, tmp_path):
        from benchmark.run_mini import save_trajectory
        out = tmp_path / "traj.jsonl"
        save_trajectory(_fake_messages(), out)
        for line in out.read_text(encoding="utf-8").strip().splitlines():
            obj = json.loads(line)
            assert isinstance(obj, dict)

    def test_each_line_has_role_and_content(self, tmp_path):
        from benchmark.run_mini import save_trajectory
        out = tmp_path / "traj.jsonl"
        save_trajectory(_fake_messages(), out)
        for line in out.read_text(encoding="utf-8").strip().splitlines():
            obj = json.loads(line)
            assert "role" in obj
            assert "content" in obj

    def test_creates_parent_dirs(self, tmp_path):
        from benchmark.run_mini import save_trajectory
        out = tmp_path / "deep" / "nested" / "traj.jsonl"
        save_trajectory(_fake_messages(), out)
        assert out.exists()


class TestRunOneTrajectory:
    """run_one() with trajectory_dir saves trajectory file at correct path."""

    def _mock_ctx(self, tmp_path: Path) -> MagicMock:
        ctx = MagicMock()
        ctx.repo_path = tmp_path
        ctx.commit_hash = "a" * 40
        (tmp_path / "src" / "rules").mkdir(parents=True)
        (tmp_path / "src" / "rules" / "rule_x.rs").write_text("// rule", encoding="utf-8")
        return ctx

    def _run_with_fake_agent(self, tmp_path, trajectory_dir):
        import sys
        from benchmark.run_mini import run_one

        fake_agent = MagicMock()
        fake_agent.cost = 0.01
        fake_agent.n_calls = 2
        fake_agent.messages = _fake_messages()

        # Patch the lazy-imported minisweagent modules via sys.modules
        mock_default = MagicMock()
        mock_default.DefaultAgent.return_value = fake_agent
        mock_local = MagicMock()
        mock_litellm = MagicMock()

        ctx = self._mock_ctx(tmp_path)
        with patch.dict(sys.modules, {
            "minisweagent.agents.default": mock_default,
            "minisweagent.environments.local": mock_local,
            "minisweagent.models.litellm_model": mock_litellm,
        }), patch("benchmark.run_mini.parse_certificate", return_value=None), \
           patch("benchmark.run_mini.extract_total_tokens", return_value=500):
            return run_one("fake/model", ctx, "rule_x", 1.0, trajectory_dir=trajectory_dir)

    def test_trajectory_file_written(self, tmp_path):
        traj_dir = tmp_path / "trajectories"
        self._run_with_fake_agent(tmp_path, traj_dir)
        safe_model = "fake_model"
        expected = traj_dir / f"{safe_model}_rule_x.jsonl"
        assert expected.exists(), f"Expected trajectory at {expected}"

    def test_no_trajectory_when_dir_is_none(self, tmp_path):
        self._run_with_fake_agent(tmp_path, None)
        assert not (tmp_path / "trajectories").exists()
