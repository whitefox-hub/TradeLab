"""The event corpus: what happened, when it was knowable, and what followed.

Separate from the operator's market trend, which is a condition to test under and has no
date. This package holds the dated, point-in-time side — the collected corpus a
study reads, the retention that bounds it, the pre-event context the risk layer
consults, and §11.3's measurement of what actually moved afterwards.

It was `tradelab.news` until the news screen was removed. The parts that went
with the screen — an expiry clock for "still influencing", a 1-10 rating scale,
a catalogue of templates, a routine-event fold — were its presentation, and they
went too. What is here is read by the engine and by studies, not by a page.
"""
