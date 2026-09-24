"""TradeLab CLI."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer

from tradelab import determinism
from tradelab.config import get_settings

app = typer.Typer(help="TradeLab — quantitative research platform", no_args_is_help=True)


@app.command()
def doctor() -> None:
    """Check that this environment can produce reproducible results (§2.2)."""
    problems = determinism.check_environment(strict=False)
    if problems:
        typer.secho("Environment is NOT pinned for reproducibility:", fg=typer.colors.RED)
        for problem in problems:
            typer.echo(f"  - {problem}")
        typer.echo("\nResults produced here must not be recorded. See Plan.md §2.2.")
        raise typer.Exit(1)
    typer.secho("Environment pinned. Results are reproducible.", fg=typer.colors.GREEN)


@app.command()
def golden() -> None:
    """Print the golden run digest (§16.6)."""
    typer.echo(determinism.golden_run())


@app.command()
def universe(limit: int = typer.Option(0, help="0 = every perpetual")) -> None:
    """Snapshot the point-in-time asset universe (§4.4)."""
    from tradelab.collectors.binance.universe import liquid_subset, snapshot

    rows = snapshot()
    selected = liquid_subset(rows, limit=limit or None)
    typer.echo(f"{len(rows)} perpetuals; {len(selected)} selected")
    for symbol in selected[:50]:
        typer.echo(f"  {symbol}")


@app.command()
def capture(
    assets: str = typer.Option(
        "", help="Comma-separated symbols for open-interest polling. Empty = top 30 by listing."
    ),
    kinds: str = typer.Option("liquidations,open_interest", help="Comma-separated"),
    root: Path | None = None,
) -> None:
    """Capture data that cannot be backfilled. Run this permanently (§4.1).

    Liquidations have no historical REST endpoint and open interest reaches back
    about 30 days, so this history exists only if something is recording. The
    liquidation stream is all-market, so it does not depend on which assets are
    configured and nothing is wasted if the universe changes later.

    Stop it with SIGTERM or Ctrl-C: buffers are flushed before exit. A SIGKILL
    loses whatever is buffered, permanently.
    """
    import asyncio

    from tradelab.collectors.binance.capture import LiquidationCapture, OpenInterestCapture
    from tradelab.collectors.supervisor import run_capture

    archive = root or get_settings().archive_root
    archive.mkdir(parents=True, exist_ok=True)
    selected = [k.strip() for k in kinds.split(",") if k.strip()]

    if assets:
        symbols = [a.strip().upper() for a in assets.split(",") if a.strip()]
    else:
        from tradelab.collectors.binance.universe import liquid_subset, snapshot

        symbols = liquid_subset(snapshot(), limit=30)

    collectors: list[tuple[object, str, str]] = []
    if "liquidations" in selected:
        collectors.append((LiquidationCapture(archive), "liquidations", "binance-forceorder"))
    if "open_interest" in selected:
        collectors.append(
            (OpenInterestCapture(archive, symbols), "open_interest", "binance-oi")
        )
    if not collectors:
        raise typer.BadParameter(f"No known kinds in {kinds!r}")

    typer.echo(f"Capturing {selected} -> {archive}")
    if "open_interest" in selected:
        typer.echo(f"  open interest polls {len(symbols)} symbols")
    asyncio.run(run_capture(collectors, "binance", root=archive))


@app.command()
def backfill(
    asset: str,
    start: str = typer.Option(..., help="YYYY-MM-DD"),
    end: str = typer.Option(..., help="YYYY-MM-DD"),
    timeframe: str = "1m",
    root: Path | None = None,
) -> None:
    """Backfill klines from data.binance.vision. No account required (§4.7)."""
    from tradelab.collectors.binance.bulk import BinanceKlineBulk
    from tradelab.collectors.runner import run_backfill

    archive = root or get_settings().archive_root
    collector = BinanceKlineBulk(timeframe)
    result = run_backfill(
        collector, asset,
        datetime.fromisoformat(start).replace(tzinfo=UTC),
        datetime.fromisoformat(end).replace(tzinfo=UTC),
        archive, timeframe=timeframe,
    )
    typer.echo(
        f"{asset} {timeframe}: {len(result.written)} files, {result.rows} rows, "
        f"{len(result.issues)} issues, {len(result.refused_days)} days refused"
    )
    for issue in result.issues[:10]:
        typer.echo(f"  [{issue.severity}] {issue.kind}: {issue.detail}")


if __name__ == "__main__":
    app()
