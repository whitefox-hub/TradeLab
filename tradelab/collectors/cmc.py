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

import os
import time
from typing import Any

from coinmarketcap import CoinMarketCap, RateLimitError

#: Bounded, because a retry loop that never gives up is a hang with a reason.
#: The documentation's own advice is to back off and retry on a rate limit.
MAX_RETRIES = 5
BACKOFF_SECONDS = 2.0

#: The free plan allows 50 requests a minute. Paced to stay under it rather
#: than discovered by hitting it: backing off after a rejection wastes the
#: request that was rejected, and a backfill of a hundred pages would spend
#: most of its time in retries. 1.3s leaves headroom for clock drift.
MIN_INTERVAL_SECONDS = 1.3
_last_call = 0.0


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


#: Read from the environment, never from a repo file. §14.4 keeps credentials
#: out of the tree, and this one is loaded the same way the operator password
#: is: `set -a && . secrets/api.env`, which is git-ignored.
KEY_VAR = "CMC_API_KEY"


def has_key() -> bool:
    return bool(os.environ.get(KEY_VAR, "").strip())


def client() -> CoinMarketCap:
    """Keyed when a key is in the environment, keyless otherwise.

    Both modes are kept working on purpose. The keyless path is what a fresh
    checkout gets, and a collector that only runs with a credential is one that
    stops silently on a machine that does not have it — so the fallback is a
    working mode and not a degraded one. What the key changes is the quota and
    the catalogue: `globalmetrics_quotes_historical` answers ForbiddenError
    without it.

    The key is never logged. It is a value that grants access, and a collector
    that prints one puts it in every log that collector ever writes.
    """
    key = os.environ.get(KEY_VAR, "").strip()
    if key:
        return CoinMarketCap(api_key=key)
    return CoinMarketCap(environment="public")


def _pace() -> None:
    """Sleep until the minimum interval since the last call has passed."""
    global _last_call
    wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def call(fn: Any, collected: list[Any] | None = None, **kwargs: Any) -> Any:
    """One SDK call, paced under the rate limit, retried through it, stopped by
    a quota."""
    delay = BACKOFF_SECONDS
    for attempt in range(MAX_RETRIES):
        try:
            _pace()
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
