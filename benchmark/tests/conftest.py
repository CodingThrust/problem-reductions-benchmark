"""Shared pytest fixtures for benchmark tests."""

import json
import pytest
from pathlib import Path


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


# ── Minimal valid results file dict ───────────────────────────────────────────

def make_results_dict(**overrides) -> dict:
    """Return a minimal schema-valid results dict, with optional field overrides."""
    base = {
        "model": "anthropic/claude-sonnet-4-6",
        "library_commit": "aa2d1a10cffa434871d12a4d6f411147fb7e08a8",
        "bugs_found": 0,
        "total_cost_usd": 0.1,
        "total_tokens_k": 10.0,
        "efficiency_bugs_per_ktok": 0.0,
        "efficiency_bugs_per_dollar": 0.0,
        "rules_tested": 1,
        "results": [
            {"rule": "some_rule", "result": "no_certificate", "cost": 0.1, "tokens_k": 10.0}
        ],
    }
    base.update(overrides)
    return base
