"""The benchmark's single built-in set of logical limits.

These values define the benchmark.  They are deliberately Python constants rather than a
configuration file: changing them changes the benchmark implementation, and the repository
commit already identifies that change.
"""

SHORTLIST_SIZE = 50
HYPOTHESIS_CHARS = 500

TRIAGE_BUDGET = {
    "model_generations": 8,
    "shell_actions": 12,
    "max_output_chars": 10_000,
    "command_timeout_seconds": 300,
}

EPISODE_BUDGET = {
    "model_generations": 10,
    "shell_actions": 12,
    "pred_calls": 24,
    "solve_calls": 10,
    "submit_attempts": 2,
    "max_output_chars": 10_000,
    "pred_timeout_seconds": 300,
}

OBSERVATION_LIMITS = {
    "preview_chars": 10_000,
    "archive_chars": 1_048_576,
}

SAFETY_CONTROLS = {
    "model_timeout_seconds": 300,
    "model_retries": 2,
}

INFERENCE_PARAMETERS = {
    "max_tokens": 8192,
    "timeout": 300,
    "num_retries": 2,
}


def benchmark_parameters() -> dict:
    """Return a fresh aggregate view for documentation and calibration checks."""
    return {
        "shortlist_size": SHORTLIST_SIZE,
        "hypothesis_chars": HYPOTHESIS_CHARS,
        "safety_controls": dict(SAFETY_CONTROLS),
        "inference_parameters": dict(INFERENCE_PARAMETERS),
        "observation": dict(OBSERVATION_LIMITS),
        "triage": dict(TRIAGE_BUDGET),
        "episode": dict(EPISODE_BUDGET),
    }
