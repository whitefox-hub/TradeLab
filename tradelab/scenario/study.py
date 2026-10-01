"""The terms of PREREG-005, in code so the analysis cannot quietly differ.

The registration is the authority; a test checks these against it. Computes nothing.
"""

from __future__ import annotations

from datetime import datetime

CUTOFF = datetime.fromisoformat("2026-09-26T14:49:00-03:00")

HORIZON_DAYS = 7

SENSITIVITY_HORIZONS = (1, 3)

MARKET = "cmc100"

MARKET_ROBUSTNESS = "BTCUSDT"

FIRST_LOOK_CALLS = 150

NEUTRAL = 5

SOURCE = "manual"
