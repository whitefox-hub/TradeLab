"""The terms of PREREG-005, in code so the analysis cannot quietly differ.

The registration is the authority; a test checks these against it. Computes nothing.
"""

from __future__ import annotations

from datetime import datetime

#: Only trend settings strictly after this (PREREG-005's commit time) count; earlier
#: entries are excluded, not deleted. A result computed without this filter is void.
CUTOFF = datetime.fromisoformat("2026-09-26T14:49:00-03:00")

#: Days; §11.5's longest horizon, truncated at the next change so observations don't overlap.
HORIZON_DAYS = 7

#: Declared sensitivity, reported once; the decision rests on HORIZON_DAYS.
SENSITIVITY_HORIZONS = (1, 3)

#: The market the trend is a view about: CMC100 (cap-weighted, 100 on 2024-01-01).
MARKET = "cmc100"

#: Robustness series, reported once; the decision rests on MARKET (FINDING-006).
MARKET_ROBUSTNESS = "BTCUSDT"

#: The first look happens here and not before (fixed stopping point; resolves ~60%).
FIRST_LOOK_CALLS = 150

#: Neutral on the 1-10 scale. A call is a change that leaves 5 or crosses it; a change to 5
#: or within one side is not.
NEUTRAL = 5

#: The only source counted; `adopted` (an accepted computed value) is excluded.
SOURCE = "manual"
