"""Tests for exact, snapshot-bounded R2 queue transitions."""
import json
from pathlib import Path

import pytest

from benchmark import r2_queue as q


def _status(incoming: Path, key: str, status: str, **extra) -> None:
    path = q.status_path(incoming, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": status, **extra}), encoding="utf-8")


def test_finished_snapshot_object_moves_to_processed(tmp_path):
    incoming = tmp_path / "incoming"
    key = "incoming/123-a.json"
    _status(incoming, key, "FINISHED")

    transition = q.plan_transition(key, incoming)

    assert transition is not None
    assert transition.source == key
    assert transition.destination == "processed/123-a.json"
    assert transition.status_destination is None


def test_permanent_failure_moves_to_failed_with_diagnostic(tmp_path):
    incoming = tmp_path / "incoming"
    key = "incoming/nested/123-b.json"
    _status(incoming, key, "FAILED", retryable=False, error="invalid JSON")

    transition = q.plan_transition(key, incoming)

    assert transition is not None
    assert transition.destination == "failed/nested/123-b.json"
    assert transition.status_destination == "failed-status/nested/123-b.status.json"


@pytest.mark.parametrize("status,extra", [
    ("RUNNING", {}),
    ("FAILED", {"retryable": True}),
    ("FAILED", {}),  # old/unknown failure statuses fail safe and remain queued
])
def test_unfinished_or_retryable_object_stays_in_incoming(tmp_path, status, extra):
    incoming = tmp_path / "incoming"
    key = "incoming/123-c.json"
    _status(incoming, key, status, **extra)
    assert q.plan_transition(key, incoming) is None


def test_missing_or_malformed_status_stays_in_incoming(tmp_path):
    incoming = tmp_path / "incoming"
    key = "incoming/123-d.json"
    assert q.plan_transition(key, incoming) is None
    path = q.status_path(incoming, key)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert q.plan_transition(key, incoming) is None


def test_only_manifest_snapshot_can_transition(tmp_path):
    incoming = tmp_path / "incoming"
    snapshotted = "incoming/123-old.json"
    arrived_later = "incoming/124-new.json"
    _status(incoming, snapshotted, "FINISHED")
    _status(incoming, arrived_later, "FINISHED")
    manifest = tmp_path / "manifest.txt"
    manifest.write_text(snapshotted + "\n", encoding="utf-8")

    transitions = q.plan_manifest(manifest, incoming)

    assert [item.source for item in transitions] == [snapshotted]


@pytest.mark.parametrize("key", ["processed/a.json", "incoming/../a.json", "incoming/"])
def test_unsafe_or_out_of_prefix_key_is_rejected(tmp_path, key):
    with pytest.raises(ValueError):
        q.status_path(tmp_path, key)
