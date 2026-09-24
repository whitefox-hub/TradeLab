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
