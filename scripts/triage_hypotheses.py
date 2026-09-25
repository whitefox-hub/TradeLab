"""Power triage of the §0.4 hypotheses, before any of them is worked on.

§8.8 refuses to run a study that cannot resolve its own target. The same
question applies one level up and was never asked there: which hypotheses can
this operator's data resolve *at all*?

Two campaigns were spent before anyone asked. Both returned inconclusive, and
both for the same reason — the design could not have answered either way. The
input here is the count of genuinely independent observations each hypothesis
can produce, which is a property of the data and is knowable in advance.
"""

from __future__ import annotations

from dataclasses import dataclass

from tradelab.validation.power import assess


@dataclass(frozen=True)
class Hypothesis:
    name: str
    independent_n: int
    per_year: float
    basis: str


#: Counts are measured where measured, and stated as estimates where not. An
#: estimate that is wrong by 2x moves the resolvable Sharpe by about 40%, so the
#: ordering below survives considerable error in these numbers.
HYPOTHESES = (
    Hypothesis(
        "1. Capacity-constrained assets", 120, 17,
        "perps ranked 30-150 by volume; independent bets limited by how many "
        "uncorrelated names exist, not by bar count",
    ),
    Hypothesis(
        "2. New listings (one venue)", 583, 78,
        "MEASURED: Binance futures listings 2019-2026. Cross-venue pooling "
        "withdrawn after RESULT-002",
    ),
    Hypothesis(
        "3. Cross-venue funding dispersion", 2_920, 365,
        "a daily dispersion signal across ~8 venues; the bet is the spread, "
        "which refreshes each funding period rather than each listing",
    ),
    Hypothesis(
        "4. Post-liquidation-cascade reversion", 400, 57,
        "cascades large enough to be mechanical are rare; count is an estimate "
        "and the feed is not currently capturing (open defect)",
    ),
    Hypothesis(
        "5. Event reaction residual", 2_077, 275,
        "MEASURED: the full announcement corpus, all classes, as the event set "
        "for an impulse response",
    ),
)


def main() -> int:
    print(f"{'hypothesis':38s} {'ind. N':>8s} {'/yr':>5s} "
          f"{'resolvable':>11s} {'N for 0.5':>10s}")
    print("-" * 76)
    for h in sorted(HYPOTHESES, key=lambda x: -x.independent_n):
        estimate = assess(target_sharpe_annual=0.5, trials=1,
                          effective_n_available=float(h.independent_n),
                          bars_per_year=h.per_year)
        needed = estimate.effective_n_needed
        years = (needed - h.independent_n) / h.per_year
        wait = f"{years:+.1f}y" if years > 0 else "have it"
        print(f"{h.name:38s} {h.independent_n:8,d} {h.per_year:5.0f} "
              f"{estimate.resolvable_sharpe_annual:11.3f} {needed:10,.0f}  {wait}")

    print("\nbasis:")
    for h in HYPOTHESES:
        print(f"  {h.name}\n      {h.basis}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
