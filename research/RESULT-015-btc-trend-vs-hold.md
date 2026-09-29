# Result 015 — BTC above its 150-day average beats holding BTC, in all three spans

Run 2026-09-29 against research/PREREG-015-btc-trend-vs-hold.md (`3cc4f0b`); the three
rules sent to the holdout were committed in `94ce404` before the holdout was read.
Output: `research/prereg_015/`.

## Search and validation

Of 24 rules, 16 beat holding BTC over 2022-01 → 2024-09 and 11 beat it in both that span
and 2024-11 → 2026-09. The three with the largest discovery excess went to the holdout.

## The holdout, 2018-04-01 → 2021-10-31, as registered

| Rule | $1 became | Worst drawdown | Days in BTC | Switches | Daily Sharpe | Beats holding |
|---|---|---|---|---|---|---|
| Hold BTC | **8.99** | −67% | 1,309 of 1,309 | 1 | 1.20 | — |
| Close > SMA 200, daily | 4.82 | −60% | 679 | 23 | 1.11 | no |
| **Close > SMA 150, daily** | **9.86** | **−45%** | 700 | 19 | **1.48** | **yes** |
| Close > SMA 200, weekly | 6.70 | −51% | 671 | 11 | 1.28 | no |

## One rule, three spans

**Long BTC while its daily close is above its 150-day average, in USDT otherwise:**

| Span | Rule | Holding | Rule's worst drawdown | Holding's |
|---|---|---|---|---|
| 2018-04 → 2021-10 (holdout) | ×9.86 | ×8.99 | −45% | −67% |
| 2022-01 → 2024-09 (discovery) | ×2.48 | ×1.35 | −21% | −67% |
| 2024-11 → 2026-09 (validation) | ×1.38 | ×1.21 | −26% | −53% |

## What it means

* **One of three passed, and narrowly in return:** ×9.86 against ×8.99 in a span where
  holding multiplied the money ninefold. With three tries, one narrow pass can be luck. The
  two 200-day rules, the neighbours of the one that passed, did not beat holding there.
* **The drawdown is the robust part.** In every span, including the two 200-day rules in
  the holdout, the trend rules fell much less than holding: −21 to −45% against −53 to
  −67%. They spend about half the time in USDT, and the long falls are where they are out.
* Beating holding on return depends on the span containing a long fall that the rule sits
  out. In a steady rise it trails, from switching and from the days it is out.
* On 2026-09-28 the rule is **long**: BTC closed at $83,500, above its 150-day average of
  $70,779, since 2026-08-19.

Next, if pursued: a forward run in the simulator beside holding BTC, under §15.

## Addendum, 2026-09-29 — the length of the average

After every span had been read, the average was scanned from 20 to 300 days in steps of
10 (holdout from 2018-07-01, so that 300 days of warm-up exist). **150 sits at the edge of
the good region:** 110 to 140 days beat holding in all three spans with wide margins,
while 150 beat it by 2% in the holdout and 160 and above did not. The centre of the region
is 120 days (×15.8, ×2.50 and ×1.50 against holding's ×9.6, ×1.35 and ×1.21; worst
drawdown −31%). Below 40 days the rule switches up to 40 times a year and costs eat it.

The 120-day rule was chosen from that scan, which used the holdout, so **no historical
figure is evidence for it**. It runs forward beside the 150-day book (`btc_trend_120`,
book `btc-sma120`) and is judged only on data after 2026-09-29. The 150-day book is
unchanged.
