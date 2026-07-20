"""Shared pytest fixtures for benchmark tests."""

import pytest


# ── Minimal valid certificate components ──────────────────────────────────────

MIS_SOURCE = {
    "data": {
        "graph": {"edges": [[0, 1], [1, 2]], "num_vertices": 3},
        "weights": [1, 1, 1],
    },
    "type": "MaximumIndependentSet",
    "variant": {"graph": "SimpleGraph", "weight": "One"},
}

# Real bundle produced by: pred create MIS --graph 0-1,1-2 | pred reduce - --to MaximumClique
MIS_TO_CLIQUE_BUNDLE = {
    "path": [
        {"name": "MaximumIndependentSet", "variant": {"graph": "SimpleGraph", "weight": "One"}},
        {"name": "MaximumClique", "variant": {"graph": "SimpleGraph", "weight": "One"}},
    ],
    "source": MIS_SOURCE,
    "target": {
        "data": {
            "graph": {"edges": [[0, 2]], "num_vertices": 3},
            "weights": [1, 1, 1],
        },
        "type": "MaximumClique",
        "variant": {"graph": "SimpleGraph", "weight": "One"},
    },
}


@pytest.fixture
def valid_unsound_cert():
    """Certificate: adjacent vertices [1,1,0] claimed as extracted from MaximumClique target."""
    return {
        "rule": "MaximumIndependentSetToMaximumClique",
        "violation": "unsound_extraction",
        "source": MIS_SOURCE,
        "bundle": MIS_TO_CLIQUE_BUNDLE,
        "target_config": "1,0,0",
        "claimed_source_solution": [1, 1, 0],
        "note": "extract returns adjacent pair (0,1) — invalid independent set",
    }


@pytest.fixture
def false_alarm_cert():
    """Certificate that claims [1,0,1] is invalid — but it IS a valid independent set."""
    return {
        "rule": "MaximumIndependentSetToMaximumClique",
        "violation": "unsound_extraction",
        "source": MIS_SOURCE,
        "bundle": MIS_TO_CLIQUE_BUNDLE,
        "target_config": "1,0,1",
        "claimed_source_solution": [1, 0, 1],
        "note": "false alarm: [1,0,1] is actually valid",
    }


@pytest.fixture
def tampered_bundle_cert():
    """Certificate whose bundle target has a fabricated extra edge — doesn't match pred."""
    tampered = {
        **MIS_TO_CLIQUE_BUNDLE,
        "target": {
            "data": {
                # Added edge [0,1] — the real complement graph of 0-1,1-2 only has edge [0,2]
                "graph": {"edges": [[0, 1], [0, 2]], "num_vertices": 3},
                "weights": [1, 1, 1],
            },
            "type": "MaximumClique",
            "variant": {"graph": "SimpleGraph", "weight": "One"},
        },
    }
    return {
        "rule": "MaximumIndependentSetToMaximumClique",
        "violation": "unsound_extraction",
        "source": MIS_SOURCE,
        "bundle": tampered,
        "target_config": "1,0,0",
        "claimed_source_solution": [1, 1, 0],
    }
