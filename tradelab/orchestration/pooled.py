"""Pooled multi-asset studies. Plan.md §6.3, §8.3, §8.8.

Pooling scores each configuration on all assets at once: trials = grid size (no choice
between assets is made), and the effective sample multiplies. Searching per asset would
cost assets x grid trials. §6.3 "validate wide, deploy narrow".
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

    Choose on `is_sharpe`, report `sharpe` (OOS); mixing them leaks selection.
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
        """Share of assets where the configuration was positive (exposes one-asset carries)."""
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
        # The grid, not grid x assets: no choice between assets was made (§8.3).
        trials=len(values) * len(entries),
    )


def _assets_under(root: Path, kind: str) -> set[str]:
    """Asset names from the partition directories (not files: the archive is huge)."""
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
