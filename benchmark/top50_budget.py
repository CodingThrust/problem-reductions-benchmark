"""Single loader for the immutable Top50 release contract."""
from __future__ import annotations

import json
from pathlib import Path

BUDGET_PATH = Path(__file__).with_name("top50_budget.json")
FROZEN_CONTRACT: dict = json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
