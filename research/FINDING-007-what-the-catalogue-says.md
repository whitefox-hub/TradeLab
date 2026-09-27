# Finding 007 — The macro catalogue is not redundant, and not connected either

Measured 2026-09-26. Ten series against CMC100, 732 overlapping days. **No
trading rule was tested**, so no trial was spent: this measures co-movement and
frequency, which are properties of the data.

## Three facts

### 1. The catalogue is not saying one thing ten times

Eight of the ten update daily. Their effective dimensionality is **4.63 of 8** —
the first factor explains 30%, the first four 84%.

Three pairs collapse, and sensibly:

| Pair | r | What it is |
|---|---|---|
| DGS10 / DGS2 | +0.80 | The yield curve, one factor |
| SP500 / VIX | −0.80 | Equities and their own volatility |
| HYSPREAD / SP500 | −0.61 | Credit moving with equity risk |

So roughly five things are being measured — rates, equity risk, the dollar,
gold, oil — and the catalogue measures them with about 4.6 independent
dimensions. It is neither redundant nor ten separate readings, which is what a
catalogue for this purpose should look like.

### 2. None of it moves with crypto, daily

| Series | r with CMC100 | In standard errors | Variance explained |
|---|---|---|---|
| HYSPREAD | −0.123 | 3.3 | **1.50%** |
| DTWEXBGS | −0.082 | 2.2 | 0.67% |
| GOLD | +0.057 | 1.5 | 0.33% |
| DGS2 | +0.049 | 1.3 | 0.24% |
| SP500 | +0.035 | 0.9 | 0.12% |
| VIX | −0.035 | 0.9 | 0.12% |
| WTI | −0.032 | 0.9 | 0.10% |
| DGS10 | +0.013 | 0.4 | 0.02% |

Two clear the Bonferroni bar for eight looks (|r| > 0.101). Both explain less
than 2% of the daily variance.

**The best series in the catalogue accounts for 1.5% of what CMC100 does. The
other 98.5% is not in any of them.**

That cuts both ways, and both need saying:

* **Against.** The rotation thesis — money leaving crypto for bonds, gold or
  equities — predicts these move against each other. They do not, at daily
  frequency, in this sample.
* **For.** A near-zero contemporaneous correlation means the information is
  **not already in the crypto price**. Had the catalogue been redundant with
  what crypto is doing, it would show up here as co-movement. It does not, so
  whatever it says is at least new.

### 3. Two of the ten cannot inform a daily reading at all

| Series | Readings a year |
|---|---|
| CPIAUCSL | 12 |
| FEDFUNDS | 12 |

Both were 57 days old when read. There is no day on which all ten series have a
fresh value — a strict join across the catalogue returns **zero rows**. Anyone
reading it is always reading a mixture of today and last month, and the screen
does not currently say which.

### 2b. Correction — the pooled figure was the wrong figure

The table above is a pooled correlation, and §13.1 says plainly to compute
correlation **per regime as well as overall**. FINDING-004 is the record of what
happens when that instruction is skipped: a pooled +0.0094 was reported as a
property of the strategy when it was a property of the regime.

The same error is in the table above. Split by VIX tercile, fixed before
looking:

| Series | Calm | Middle | **Stress** | Pooled |
|---|---|---|---|---|
| **DTWEXBGS** (dollar) | **+0.125** | −0.161 | **−0.171** | −0.082 |
| **HYSPREAD** (credit) | −0.052 | −0.161 | **−0.127** | −0.123 |
| DGS2 | +0.177 | −0.036 | +0.008 | +0.049 |
| The other five | noise | noise | noise | ~0 |

The dollar **changes sign**: it moves with crypto in calm and against it in
stress, and the pooled average cancels the two. That is the rotation thesis,
appearing exactly where §13.1 says to look for it and nowhere else.

**And it does not survive being counted honestly.** At 244 days a bucket the
standard error is 0.064, so a single look needs |r| > 0.125 — but this is eight
series across three buckets, twenty-four looks, and the corrected bar is
**0.197**. Nothing reaches it:

| | r | In SE | One look | Corrected |
|---|---|---|---|---|
| DTWEXBGS, stress | −0.171 | 2.7 | clears | **does not** |
| HYSPREAD, stress | −0.127 | 2.0 | clears | **does not** |
| DGS2, calm | +0.177 | 2.8 | clears | **does not** |

And this is now the **second** cut of the same data. The first was justified —
§13.1 instructs it — but the bar rose for taking it, and a third would be
searching. The right response to a suggestive pattern at twenty-four looks is a
registration, not another slice.

## So does it contribute anything?

**Something, in the direction the screen claims, and not enough to rely on.**

Pooled, there is no daily relationship. Split by regime — which is what §13.1
requires and what the first version of this finding failed to do — the dollar
and the credit spread move against crypto in stress and the dollar flips sign
between calm and stress. That is the rotation thesis, and it is where the thesis
says it should be.

It is also 2.7 standard errors on the best of twenty-four looks, against a
corrected bar of 0.197 that none of them clears. Suggestive, unestablished, and
the honest word for it is *unmeasured* rather than *absent*.

**Its value rests entirely on PREREG-005.** The claimed mechanism is not
statistical — it is that the operator reads these numbers, forms a view, and
enters it as the market trend. That is a claim about the operator, and it is
exactly what PREREG-005 measures. If the operator's readings carry directional
information, the catalogue is their input and earns its place. If they do not,
this is a wall of numbers with a 1.5% ceiling behind it.

What this finding removes is the *strength* of the intermediate justification,
not the justification itself. "Macro matters because money rotates" is visible
in stress and only there, at a size this sample cannot establish. Anyone saying
it should say which regime they mean and that the evidence is 2.7 standard
errors on a twenty-four-look search.

## What it does not say

Not that macro is irrelevant to crypto. Daily co-movement is one frequency and a
weak test; a relationship at weekly or monthly horizons, or one that appears
only in stress, would not show here. Measuring that is a **trial** and needs a
registration — and given §0.4a's arithmetic and the breadth of one that a single
global reading carries, it would be an expensive one to spend.
