"""Stable filesystem labels for benchmark artifacts."""


def safe_model_label(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")
