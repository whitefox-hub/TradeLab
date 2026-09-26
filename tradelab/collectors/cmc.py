"""One way in to CoinMarketCap. Plan.md §4.3, §11.4.

Every CMC series here goes through the official SDK in its keyless `public`
mode, so throttling and error shape are handled once rather than per collector.

**Why the SDK and not the twenty lines of httpx it replaced.** The hand-rolled
version had to recognise two different throttles by matching their English
messages — "You've reached the limit for anonymous access" is a ceiling a key
lifts, "You've hit an IP rate limit" is a speed to wait out — because both
arrive as a 200 with the message inside the payload. That works until CMC
rewords one. The SDK raises `RateLimitError` and `ForbiddenError` instead, which
is the same information as a type.

What did **not** move here is the domain: how a reading is stamped, what counts
as a change, which band names are the same band. That is where the errors this
project cares about live, and it stays in the collectors that own it.
"""

from __future__ import annotations

import time
from typing import Any

from coinmarketcap import CoinMarketCap, RateLimitError

#: Bounded, because a retry loop that never gives up is a hang with a reason.
#: The documentation's own advice is to back off and retry on a rate limit.
MAX_RETRIES = 5
BACKOFF_SECONDS = 2.0


class QuotaReached(RuntimeError):
    """Anonymous access ran out. Carries whatever was collected before it did.

    A ceiling, not a speed: waiting does not move it, and only a key does. It
    carries the partial result because the first version of the backfill let
    the error escape and threw away ninety pages of work with it, so every
    re-run restarted from inception and hit the same wall in the same place.
    """

    def __init__(self, collected: list[Any] | None = None) -> None:
        super().__init__(
            f"anonymous quota reached after {len(collected or [])} items; "
            "re-run to resume, or use a free API key")
        self.collected = collected or []


def client() -> CoinMarketCap:
    """Keyless. `public` is the base every endpoint on the keyless list serves
    from; the ones that are not on it raise ForbiddenError, which is how
    `globalmetrics_quotes_historical` turned out to need a key."""
    return CoinMarketCap(environment="public")


def call(fn: Any, collected: list[Any] | None = None, **kwargs: Any) -> Any:
    """One SDK call, retried through a rate limit and stopped by a quota."""
    delay = BACKOFF_SECONDS
    for attempt in range(MAX_RETRIES):
        try:
            return fn(**kwargs)
        except RateLimitError:
            # The SDK reports both throttles this way, so the two are told
            # apart by whether waiting helps: retries that all fail are a
            # ceiling in practice, whatever the message called it.
            if attempt == MAX_RETRIES - 1:
                raise QuotaReached(collected) from None
            time.sleep(delay)
            delay *= 2
    raise QuotaReached(collected)
