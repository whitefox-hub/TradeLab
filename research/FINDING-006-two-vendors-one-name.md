# Finding 006 — Two fear-and-greed indices disagree about the band on two days in five

Measured 2026-09-26 on 1185 overlapping days, 2023-06-29 to 2026-09-25.
CoinMarketCap's index against alternative.me's, both collected here, both
published under the name "Fear and Greed Index" with the same five band names.

## The numbers

| | |
|---|---|
| Overlapping days | 1185 |
| Correlation of levels | **0.902** |
| Mean difference | −0.46 |
| Standard deviation of the difference | **9.43** |
| Range | −27 to **+42** |
| Days they agree exactly | **5.0%** |
| Days more than 5 points apart | 52.1% |
| Days more than 10 points apart | 24.4% |
| **Days they name the same band** | **58.6%** |

A correlation of 0.90 says they are measuring something similar. Everything else
says they are not measuring the same thing.

## Why the band number is the one that matters

§11.4 puts market state into **discrete buckets**, not a continuous interaction,
because sample size forbids conditioning on a continuum. So any rule that ever
reads this reads the band, not the number.

**The two vendors name a different band on 41.4% of days.** A rule that holds
back under "Extreme Fear" fires on a different set of days depending on which
index it was wired to, and nothing downstream would reveal which one it was —
both produce a plausible label for every day of the sample.

That is not a tie to be broken. It is two black boxes disagreeing, and picking
the one that backtests better is choosing a vendor by its results.

## This is §11.4's argument, measured

The Plan already refused to buy one:

> Built in-house, not bought. A vendor fear-and-greed index is a black box, is
> partly composed of inputs already held, and can be revised — which breaks
> point-in-time correctness (§4.3).

That was an argument. This is the size of it. Two vendors, the same name, the
same five bands, disagreeing on the label two days in five over three years.

## What is done about it, and what is not

Both series are collected and **kept apart**, keyed on `(day, source)`. Neither
is averaged into the other: an average of two methods is a third method nobody
can decompose, and it would hide exactly the disagreement measured here.

Both are stored outside `macro_series`, with no foreign key into the corpus, so
a study cannot reach one by accident. They are there to be looked at.

**Nothing here conditions on either of them**, and this finding is the reason to
keep it that way until §11.4's own index exists. The state vector it describes —
funding z-score and cross-venue dispersion, liquidation asymmetry, trade count
and average trade size, depth and effective spread — is decomposable when it
behaves oddly, which is the property both of these lack.

## A smaller thing, found on the way

CoinMarketCap's own two endpoints disagree about capitalisation: `latest`
returns `"Extreme Greed"` and `historical` returns `"Extreme greed"`. Stored
verbatim they would have become two bands in any group-by, and a band would
have appeared to lose most of its days while both spellings looked correct.

The collector normalises case and refuses anything else. It found this by
refusing: the first backfill failed with `unknown classification 'Extreme
greed'` rather than writing 1185 rows nobody would have re-read.
