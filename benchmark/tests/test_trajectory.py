"""
Tests for the normalized session trajectory archive.

All tests are marked @pytest.mark.judgment.
"""
import json
import pytest

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
