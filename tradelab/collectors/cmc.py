"""Single access point to CoinMarketCap via the official SDK (Plan.md §4.3, §11.4).

Uses the SDK because CMC throttles arrive as HTTP 200 with a message; the SDK raises
typed errors. Domain logic (stamping, bands) stays in the collectors."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from coinmarketcap import CoinMarketCap, RateLimitError

MAX_RETRIES = 5
BACKOFF_SECONDS = 2.0

MIN_INTERVAL_SECONDS = 1.3
_last_call = 0.0

MAX_REQUESTS_PER_RUN = 200
_spent = 0


class BudgetExceeded(RuntimeError):
    """A run asked for more requests than it is allowed in one go."""


@dataclass(frozen=True, slots=True)
class Budget:
    used_month: int
    left_month: int
    used_today: int
    limit_month: int
    per_minute: int

    @property
    def share_used(self) -> float:
        return self.used_month / self.limit_month if self.limit_month else 0.0


def budget() -> Budget | None:
    """Vendor-reported credit usage; None without a key. Costs zero credits (measured)."""
    key = os.environ.get(KEY_VAR, "").strip()
    if not key:
        return None
    payload = httpx.get(
        "https://pro-api.coinmarketcap.com/v1/key/info",
        headers={"X-CMC_PRO_API_KEY": key}, timeout=20.0).json()["data"]
    plan, usage = payload["plan"], payload["usage"]
    return Budget(
        used_month=usage["current_month"]["credits_used"],
        left_month=usage["current_month"]["credits_left"],
        used_today=usage["current_day"]["credits_used"],
        limit_month=plan.get("credit_limit_monthly") or 0,
        per_minute=plan.get("rate_limit_minute") or 0,
    )


def spent() -> int:
    """Requests made since the last reset. One credit each."""
    return _spent


def reset_budget() -> None:
    global _spent
    _spent = 0


class QuotaReached(RuntimeError):
    """Anonymous quota exhausted (only a key lifts it). Carries the partial result so a
    backfill keeps what it fetched."""

    def __init__(self, collected: list[Any] | None = None) -> None:
        super().__init__(
            f"anonymous quota reached after {len(collected or [])} items; "
            "re-run to resume, or use a free API key")
        self.collected = collected or []


KEY_VAR = "CMC_API_KEY"


def client() -> CoinMarketCap:
    """Keyed when a key is in the environment, keyless otherwise; both must keep working.

    Keyless lacks e.g. `globalmetrics_quotes_historical` (ForbiddenError). Never log the key."""
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
    """One SDK call: paced, retried on rate limit, counted against the run ceiling.

    Counted before the request: the vendor charges for attempts."""
    global _spent
    if _spent >= MAX_REQUESTS_PER_RUN:
        raise BudgetExceeded(
            f"{_spent} requests in one run, ceiling {MAX_REQUESTS_PER_RUN}. "
            "Raise it deliberately or split the work; a run that needs more "
            "than this is usually a loop that is not ending.")
    _spent += 1
    delay = BACKOFF_SECONDS
    for attempt in range(MAX_RETRIES):
        try:
            _pace()
            return fn(**kwargs)
        except RateLimitError:
            if attempt == MAX_RETRIES - 1:
                raise QuotaReached(collected) from None
            time.sleep(delay)
            delay *= 2
    raise QuotaReached(collected)
