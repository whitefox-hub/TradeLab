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

## So does it contribute anything?

**Not through any channel measured here.** There is no daily relationship
between the catalogue and crypto, and the rotation reading the screen is built
around does not show up in the data at this frequency.

**Its value rests entirely on PREREG-005.** The claimed mechanism is not
statistical — it is that the operator reads these numbers, forms a view, and
enters it as the market trend. That is a claim about the operator, and it is
exactly what PREREG-005 measures. If the operator's readings carry directional
information, the catalogue is their input and earns its place. If they do not,
this is a wall of numbers with a 1.5% ceiling behind it.

What this finding removes is the *intermediate* justification: nobody should say
"macro matters because money rotates" and point at these series. In this sample
it does not, and the honest version of the argument is "the operator may see
something a correlation does not", which is a hypothesis with a registration
waiting on it.

## What it does not say

Not that macro is irrelevant to crypto. Daily co-movement is one frequency and a
weak test; a relationship at weekly or monthly horizons, or one that appears
only in stress, would not show here. Measuring that is a **trial** and needs a
registration — and given §0.4a's arithmetic and the breadth of one that a single
global reading carries, it would be an expensive one to spend.
