"""Acceptance tests for deterministic high-information observation packaging."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from benchmark.observation_policy import ObservationConfig, ObservationStore, POLICY_ID


def _fixture() -> str:
    parts = ["\x1b[32mstarting tests\x1b[0m\n", "progress 1%\rprogress 50%\rprogress 100%\n"]
    parts.extend("test common_case ... ok\n" for _ in range(300))
    parts.extend(f"filler {index:04d} " + "x" * 80 + "\n" for index in range(180))
    parts.extend([
        "context before one\n",
        "context before two\n",
        "SENTINEL_FAILURE_AT_TAIL\n",
        "assertion failed: left = 17\n",
        "right = 19\n",
        "context after one\n",
        "context after two\n",
        "2 failed, 325 passed\n",
    ])
    return "".join(parts)


def _package(tmp_path: Path, text: str, *, returncode: int = 1):
    work = tmp_path / "work"
    work.mkdir()
    store = ObservationStore(
        tmp_path / "observations",
        config=ObservationConfig(preview_chars=10_000, archive_chars=64_000),
        relative_from=work,
        readable_gid=os.getgid(),
    )
    return work, store.package(
        kind="shell", command="pytest -q", returncode=returncode, timed_out=False,
        stdout=text, stderr="", original_chars=len(text),
        original_lines=text.count("\n"), archive_truncated=False)


def test_known_terminal_stream_retains_tail_diagnostics_and_exact_metadata(tmp_path, capsys):
    fixture = _fixture()
    work, packaged = _package(tmp_path, fixture)
    preview, metadata = packaged.preview, packaged.metadata

    print(preview)
    assert len(preview) <= 10_000
    assert "\x1b" not in preview
    assert "progress 1%" not in preview and "progress 50%" not in preview
    assert "progress 100%" in preview
    assert preview.count("test common_case ... ok") == 1
    assert "repeated 300 times" in preview
    for expected in (
        "context before one", "context before two", "SENTINEL_FAILURE_AT_TAIL",
        "left = 17", "right = 19", "context after one", "context after two",
        "2 failed, 325 passed", "returncode=1",
    ):
        assert expected in preview
    assert re.search(r"\[L\d{5}\] SENTINEL_FAILURE_AT_TAIL", preview)
    assert metadata == {
        "observation_id": "shell-0001",
        "kind": "shell",
        "command": "pytest -q",
        "policy_id": POLICY_ID,
        "raw_log": "../observations/shell-0001.log",
        "returncode": 1,
        "timed_out": False,
        "original_chars": len(fixture),
        "original_lines": fixture.count("\n"),
        "preview_chars": len(preview),
        "archive_chars": len(fixture),
        "preview_compacted": True,
        "archive_truncated": False,
    }
    raw = (work / metadata["raw_log"]).resolve()
    assert raw.read_bytes().decode("utf-8") == fixture
    assert raw.stat().st_mode & 0o060 == 0o040
    assert "SENTINEL_FAILURE_AT_TAIL" in capsys.readouterr().out


def test_prefix_only_negative_control_loses_tail_sentinel(tmp_path):
    fixture = _fixture()
    prefix_only = fixture[:10_000]
    assert "SENTINEL_FAILURE_AT_TAIL" not in prefix_only
    _, packaged = _package(tmp_path, fixture)
    assert "SENTINEL_FAILURE_AT_TAIL" in packaged.preview


def test_middle_diagnostic_survives_when_long_head_and_tail_exceed_preview(tmp_path):
    head = "".join(f"head-{index} " + "h" * 2_000 + "\n" for index in range(12))
    middle = "middle context before\nUNIQUE_ERROR_IN_MIDDLE\nmiddle context after\n"
    tail = "".join(f"tail-{index} " + "t" * 2_000 + "\n" for index in range(12))
    _, packaged = _package(tmp_path, head + middle + tail)

    assert len(packaged.preview) <= 10_000
    assert "UNIQUE_ERROR_IN_MIDDLE" in packaged.preview
    assert "middle context before" in packaged.preview
    assert "middle context after" in packaged.preview


def test_successful_test_summary_survives_many_error_named_tests(tmp_path):
    text = "".join(f"test_error_case_{index} PASSED\n" for index in range(500))
    text += "500 passed, 3 deselected in 12.34s\n"
    _, packaged = _package(tmp_path, text, returncode=0)

    assert len(packaged.preview) <= 10_000
    assert "500 passed, 3 deselected" in packaged.preview


def test_valid_json_is_minified_without_losing_values(tmp_path):
    text = '{\n  "type": "MaximumIndependentSet",\n  "weights": [1, 2, 3]\n}\n'
    _, packaged = _package(tmp_path, text, returncode=0)
    assert '{"type":"MaximumIndependentSet","weights":[1,2,3]}' in packaged.preview
    assert packaged.metadata["preview_compacted"] is True


def test_archive_is_bounded_and_disappears_with_episode_root(tmp_path):
    episode = tmp_path / "episode"
    fixture = "head\n" + "x" * 100_000 + "\ntail\n"
    work = episode / "work"
    work.mkdir(parents=True)
    store = ObservationStore(
        episode / "observations",
        config=ObservationConfig(preview_chars=1_000, archive_chars=4_000),
        relative_from=work,
    )
    packaged = store.package(
        kind="shell", command="spam", returncode=0, timed_out=False,
        stdout=fixture, stderr="", original_chars=len(fixture), original_lines=3,
        archive_truncated=False)
    raw = (work / packaged.metadata["raw_log"]).resolve()
    assert raw.stat().st_size <= 4_000
    assert packaged.metadata["archive_truncated"] is True
    assert len(packaged.preview) <= 1_000
    # Production sessions remove their random episode root before the next episode starts.
    import shutil
    shutil.rmtree(episode)
    assert not raw.exists()


def test_caps_too_small_for_observation_metadata_are_rejected():
    with pytest.raises(ValueError, match="preview_chars >= 256"):
        ObservationConfig(preview_chars=1, archive_chars=1)
