"""The terms of PREREG-005, in code so the analysis cannot quietly differ.

The registration is the authority; this is the part of it a script has to obey,
lifted out so obeying it is an import rather than a memory. A study whose
parameters live only in prose is one where the run and the registration drift
and nobody can tell which came first.

Nothing here computes a result. The measurement happens when there are calls to
measure, and the count that opens it is below.
"""

from __future__ import annotations

from datetime import datetime

#: Only trend settings recorded strictly after this count as observations.
#:
#: The commit timestamp of PREREG-005 itself. Entries before it were made while
#: testing the endpoint; they are excluded by this clause rather than deleted,
#: because an audit log that can be tidied is not one. A result computed without
#: this filter is void.
CUTOFF = datetime.fromisoformat("2026-09-26T14:49:00-03:00")

#: Days. The longest horizon on §11.5's grid, taken from there rather than
#: chosen here, and truncated at the next change so observations do not overlap.
HORIZON_DAYS = 7

#: Reported in full as a declared sensitivity, once. The decision rests on
#: HORIZON_DAYS whatever these say; running them is not a sweep, choosing
#: between them afterwards would be.
SENSITIVITY_HORIZONS = (1, 3)

#: The market the trend is a view about. A poor proxy in an alt-led move, and
#: used because it is the series already collected at this resolution.
MARKET = "BTCUSDT"

#: The first look happens here and not before.
#:
#: Fixed so the stopping point cannot be chosen by watching the hit rate. At 150
#: directional calls the study resolves a true rate of about 60%; at 30 it
#: resolves only 72%, which nobody has on macro direction — so an early look is
#: not an early answer, it is a guaranteed inconclusive being read as evidence.
FIRST_LOOK_CALLS = 150

#: A change to NORMAL is the absence of a call, not a call.
DIRECTIONAL = ("UP", "DOWN")
