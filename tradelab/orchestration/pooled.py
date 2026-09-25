"""Pooled multi-asset studies. Plan.md §6.3, §8.3, §8.8.

The distinction this module exists for is subtle and decides whether a study can
conclude anything.

**Searching across assets**: run a grid on each of 50 assets and keep whichever
asset it worked on. That is 50 x grid trials, the bar rises with all of them, and
the survivor has to clear a height set by the whole search.

**Pooling across assets**: evaluate each configuration on all 50 assets and score
it by the pooled result. That is grid trials — the asset count does not enter it,
because no choice was made between assets — and the effective sample multiplies.

The second is what §6.3 means by "validate wide, deploy narrow", and the
arithmetic is decisive. Measured on a real study: at 2,843 effective
observations, the required annualised Sharpe is 4.14 after 532 trials and 2.78
after nine. The study produced 2.29, so trimming the grid alone would not have
been enough. Required Sharpe falls as 1/sqrt(n), so pooling is the lever that
moves it: crypto assets correlate around 0.75, and fifty of them behave like six
or eight independent ones — enough to drop the bar by a factor of roughly 2.6.

Pooling is also the more honest claim. "This works on BTC" invites the question
of how many assets were tried; "this works across the universe" does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

from tradelab.engines.tier_a.costs import BINANCE_RETAIL, CostModel
from tradelab.engines.tier_a.engine import run, threshold_position
from tradelab.features.materialise import materialise


@dataclass
class PooledConfig:
    """One configuration, scored separately in and out of sample.

    Both are kept because they answer different questions and mixing them is how
    selection leakage enters. `is_sharpe` is what a configuration may be CHOSEN
    on; `sharpe` is what the chosen one is REPORTED as. Picking the best
    out-of-sample score and then quoting that score is choosing with the test set
    in hand and then citing the test as evidence.
    """

    params: dict
    pooled_returns: np.ndarray          # out of sample
    is_returns: np.ndarray              # in sample, for selection only
    per_asset_sharpe: dict[str, float]
    total_trades: int
    effective_n: float

    @property
    def sharpe(self) -> float:
        std = self.pooled_returns.std(ddof=1)
        return float(self.pooled_returns.mean() / std) if std > 0 else 0.0

    @property
    def is_sharpe(self) -> float:
        std = self.is_returns.std(ddof=1) if self.is_returns.size > 1 else 0.0
        return float(self.is_returns.mean() / std) if std > 0 else 0.0

    @property
    def breadth(self) -> float:
        """Share of assets where the configuration was positive.

        The number that separates a configuration that works from one that was
        carried by a single asset. A pooled Sharpe can look respectable while
        forty-nine assets lose and one wins enormously, and breadth is what makes
        that visible.
        """
        if not self.per_asset_sharpe:
            return 0.0
        return float(np.mean([s > 0 for s in self.per_asset_sharpe.values()]))


@dataclass
class PooledResult:
    configs: list[PooledConfig]
    assets: list[str]
    trials: int
    field_names: list[str] = field(default_factory=list)

    def best(self) -> PooledConfig:
        """Selected on IN-SAMPLE score. Its out-of-sample figure is the result."""
        return max(self.configs, key=lambda c: c.is_sharpe)

    def most_consistent(self) -> PooledConfig:
        """Highest breadth, tie-broken by Sharpe.

        Usually the better thing to promote: a configuration positive across
        forty assets with a modest Sharpe is a different claim from one carried
        by a single outlier.
        """
        return max(self.configs, key=lambda c: (round(c.breadth, 2), c.is_sharpe))


def run_pooled(
    root: Path, assets: list[str], feature: str, param_name: str,
    values: tuple, entries: tuple[float, ...], timeframe: str = "4h",
    extra_params: dict | None = None, direction: int = 1,
    costs: CostModel | None = None, min_trades_per_asset: int = 20,
    oos_fraction: float = 0.3, years: float | None = None,
) -> PooledResult:
    """Evaluate every configuration on every asset, scored on the pooled result."""
    from tradelab.features import REGISTRY
    from tradelab.orchestration.study import load_bars

    costs = costs or BINANCE_RETAIL
    extra = extra_params or {}
    definition = REGISTRY.get(feature)

    loaded: dict[str, tuple[np.ndarray, np.ndarray, dict[object, np.ndarray]]] = {}
    for asset in assets:
        try:
            bars = load_bars(root, asset, timeframe, years=years)
        except Exception:
            continue
        if bars.height < 2_000:
            continue
        features = materialise(
            bars, [(feature, {param_name: v, **extra}) for v in values],
            keep=("event_time", "close"),
        )
        close = bars["close"].to_numpy().astype(np.float64)
        funding = (
            bars["funding_rate"].fill_null(0.0).to_numpy().astype(np.float64)
            if "funding_rate" in bars.columns else None
        )
        signals = {
            v: features[definition.column({param_name: v, **extra})]
            .to_numpy().astype(np.float64)
            for v in values
        }
        loaded[asset] = (close, funding, signals)

    configs: list[PooledConfig] = []
    for value in values:
        for entry in entries:
            pooled: list[np.ndarray] = []
            in_sample: list[np.ndarray] = []
            per_asset: dict[str, float] = {}
            trades = 0
            effective = 0.0

            for asset, (close, funding, signals) in loaded.items():
                split = int(len(close) * (1 - oos_fraction))
                position = threshold_position(signals[value], float(entry)) * direction
                result = run(close[split:], position[split:], costs, timeframe,
                             funding=funding[split:] if funding is not None else None)
                if result.n_trades < min_trades_per_asset:
                    continue

                returns = np.zeros(len(close) - split)
                returns[1:] = np.diff(close[split:]) / close[split:-1]
                lagged = np.concatenate([[0.0], position[split:-1]])
                traded = np.abs(np.diff(np.concatenate([[0.0], lagged])))
                net = lagged * returns - traded * costs.per_side
                if funding is not None:
                    net = net - lagged * funding[split:]

                pooled.append(net)

                # The same computation on the training window, for selection.
                is_returns = np.zeros(split)
                is_returns[1:] = np.diff(close[:split]) / close[:split - 1]
                is_lagged = np.concatenate([[0.0], position[:split - 1]])
                is_traded = np.abs(np.diff(np.concatenate([[0.0], is_lagged])))
                is_net = is_lagged * is_returns - is_traded * costs.per_side
                if funding is not None:
                    is_net = is_net - is_lagged * funding[:split]
                in_sample.append(is_net)

                per_asset[asset] = result.sharpe
                trades += result.n_trades
                effective += result.effective_n

            if pooled:
                configs.append(PooledConfig(
                    params={param_name: value, "entry": float(entry)},
                    pooled_returns=np.concatenate(pooled),
                    is_returns=np.concatenate(in_sample) if in_sample else np.zeros(0),
                    per_asset_sharpe=per_asset, total_trades=trades,
                    effective_n=effective,
                ))

    return PooledResult(
        configs=configs, assets=sorted(loaded),
        # The grid, not the grid times the assets: no choice was made between
        # assets, so none is paid for (§8.3).
        trials=len(values) * len(entries),
    )


def _assets_under(root: Path, kind: str) -> set[str]:
    """Asset names from the partition directories.

    Walks directories rather than files: the archive holds 439,000 parquet files
    and the layout already answers which assets exist, so listing them is a few
    hundred stats instead of half a million.
    """
    base = root / kind
    if not base.exists():
        return set()
    found = set()
    for depth in ("asset=*", "*/asset=*", "*/*/asset=*"):
        for directory in base.glob(depth):
            if directory.is_dir():
                found.add(directory.name.split("=", 1)[1])
        if found:
            break
    return found


def funding_universe(root: Path, limit: int | None = None) -> list[str]:
    """Assets carrying both OHLCV and funding."""
    both = sorted(_assets_under(root, "ohlcv") & _assets_under(root, "funding"))
    return both[:limit] if limit else both


def summarise(result: PooledResult) -> pl.DataFrame:
    return pl.DataFrame([
        {**c.params, "is_sharpe": c.is_sharpe, "oos_sharpe": c.sharpe,
         "breadth": c.breadth,
         "trades": c.total_trades, "effective_n": c.effective_n,
         "assets": len(c.per_asset_sharpe)}
        for c in result.configs
    ]).sort("is_sharpe", descending=True)
