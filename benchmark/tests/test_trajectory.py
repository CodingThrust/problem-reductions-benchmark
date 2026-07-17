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


class TestTrajectoryWriter:
    """Incremental (per-step) trajectory flushing — the live view of a running session."""

    def test_incremental_appends(self, tmp_path):
        from benchmark.run_mini import TrajectoryWriter
        out = tmp_path / "traj.jsonl"
        w = TrajectoryWriter(out)
        msgs = _fake_messages()
        w.flush(msgs[:2])
        w.flush(msgs)
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == len(msgs)

    def test_no_duplicate_lines_on_repeat_flush(self, tmp_path):
        from benchmark.run_mini import TrajectoryWriter
        out = tmp_path / "traj.jsonl"
        w = TrajectoryWriter(out)
        msgs = _fake_messages()
        w.flush(msgs)
        w.flush(msgs)
        assert len(out.read_text(encoding="utf-8").strip().splitlines()) == len(msgs)

    def test_starts_fresh_not_appending_stale(self, tmp_path):
        from benchmark.run_mini import TrajectoryWriter, save_trajectory
        out = tmp_path / "traj.jsonl"
        save_trajectory(_fake_messages(), out)  # leftover from a previous run
        w = TrajectoryWriter(out)
        w.flush(_fake_messages()[:1])
        assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 1

    def test_matches_final_save_format(self, tmp_path):
        from benchmark.run_mini import TrajectoryWriter, save_trajectory
        msgs = _fake_messages()
        incremental = tmp_path / "inc.jsonl"
        TrajectoryWriter(incremental).flush(msgs)
        final = tmp_path / "final.jsonl"
        save_trajectory(msgs, final)
        assert incremental.read_text(encoding="utf-8") == final.read_text(encoding="utf-8")
