"""High-impact event templates. Plan.md §11.7, §20.5.

A catalogue of the events an operator most wants to record quickly, each with
the shape it actually is and a **suggested** strength and direction.

**Suggested, and the word is load-bearing.** A template that pre-fills a rating
and is accepted without thought measures the template rather than the operator,
and §20.5's whole reason for storing ratings in sigma is that the *annotator's*
calibration becomes knowable. So an annotation created from a template records
that it came from one, and a rating left exactly at the suggestion is
distinguishable afterwards from one the operator moved. A calibration curve that
cannot tell those apart is measuring this file.

**Shapes are assigned, not guessed.** A war breaking out and a war ending are
both regime shifts — neither deviates from a baseline and returns to it, they
replace one. A rate decision is a schedule: the date is known and the market
prices toward it. A data print is a shock. Each needs different machinery, and
filing a regime shift as a long shock fits a decay to a reversion that never
happens.

**Market-wide, almost all of them.** §11.3's cross-sectional benchmark needs an
unaffected asset to difference against, and for a war or a systemic banking
crisis there is none — so these carry `market_wide`, which sends the measurement
to the weaker time-series counterfactual rather than letting it pretend.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Template:
    key: str
    label: str
    event_type: str
    shape: str
    #: 1-10 on the scale of `strength.py`; direction carries the sign.
    strength: int
    direction: int
    market_wide: bool = True
    note: str = ""

    @property
    def signed(self) -> int:
        """The catalogue's own notation, for display: -10 to +10."""
        return self.strength * self.direction


#: Ordered by severity within each direction, which is how an operator scans it
#: under time pressure — the thing that just happened is near the top or near
#: the bottom, not buried in the middle.
CATALOGUE: tuple[Template, ...] = (
    Template("war_major_powers", "Escalada militar grave entre grandes potencias",
             "war", "regime_shift", 10, -1,
             note="Replaces the baseline. No decay is fitted to it."),
    Template("war_us_iran", "Guerra / escalada grave EE.UU.-Iran, cierre de Hormuz",
             "war", "regime_shift", 10, -1,
             note="Energy shock on top of the geopolitical one."),
    Template("banking_crisis", "Crisis bancaria o financiera sistémica",
             "financial_crisis", "regime_shift", 10, -1,
             note="Correlations converge toward one; the crisis-regime "
                  "correlation is the number that matters, not the average."),
    Template("russia_ukraine", "Escalada importante Rusia-Ucrania / ampliación",
             "war", "regime_shift", 8, -1),
    Template("trade_war", "Guerra comercial / aranceles extremos EE.UU.-China",
             "trade_policy", "process", 8, -1,
             note="A process: it resolves in steps, and a step can supersede "
                  "the one before rather than adding to it."),
    Template("inflation_hot", "Inflación muy superior a expectativas + Fed endurece",
             "macro_release", "shock", 7, -1,
             note="A print, so a shock — and its surprise is only a number if a "
                  "consensus was recorded before it."),
    Template("recession", "Recesión / datos mucho peores de lo esperado",
             "macro_release", "shock", 7, -1),
    Template("regulation_hostile", "Regulación hostil / prohibición",
             "regulation", "regime_shift", 8, -1,
             note="A ban replaces the baseline rather than deviating from it."),
    Template("exchange_collapse", "Colapso de un exchange importante",
             "exchange_collapse", "regime_shift", 9, -1),
    Template("peace_deal", "Acuerdo de paz importante / desescalada geopolítica",
             "peace", "regime_shift", 8, +1,
             note="The end of a regime is its own regime shift: the market "
                  "rarely returns to where it was before."),
    Template("fed_easing", "Fed inicia fuertes recortes / expansión de liquidez",
             "monetary_policy", "schedule", 8, +1,
             note="A schedule: the decision date is known and the market prices "
                  "toward it, so the tradeable object is the approach."),
    Template("trade_deal", "Acuerdo comercial importante EE.UU.-China",
             "trade_policy", "process", 7, +1),
    Template("crypto_regulation_good", "Regulación favorable importante para cripto",
             "regulation", "regime_shift", 7, +1),
    Template("ai_breakthrough", "Revolución tecnológica / resultados de IA",
             "technology", "shock", 6, +1, market_wide=False,
             note="Not market-wide: it moves a sector, so a cross-sectional "
                  "benchmark still exists."),
)

BY_KEY = {template.key: template for template in CATALOGUE}
