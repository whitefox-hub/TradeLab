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

    Stop with SIGTERM or Ctrl-C to flush buffers; SIGKILL loses them.
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


@app.command("backfill-universe")
def backfill_universe_cmd(
    limit: int = typer.Option(0, help="0 = every eligible contract"),
    min_history_days: int = typer.Option(365, help="Skip contracts younger than this"),
    timeframe: str = "1m",
    workers: int = 6,
    root: Path | None = None,
) -> None:
    """Backfill klines for the whole universe, from each contract's listing date.

    Resumable: an existing (immutable) partition is complete and skipped.
    """
    from datetime import date as _date
    from datetime import timedelta as _td

    from tradelab.collectors.backfill import backfill_universe, listing_dates

    archive = root or get_settings().archive_root
    archive.mkdir(parents=True, exist_ok=True)
    assets = listing_dates(limit=limit or None, min_history_days=min_history_days)
    # Yesterday: today's daily archive may not be published yet.
    end = _date.today() - _td(days=1)

    typer.echo(f"{len(assets)} contracts, {timeframe}, through {end} -> {archive}")
    total = backfill_universe(assets, end, archive, timeframe, workers)
    typer.echo(
        f"done: {total.days_written} days written, {total.days_skipped} already present, "
        f"{total.days_absent} before listing, {total.rows:,} rows, "
        f"{total.bytes_written / 2**30:.2f} GB, {len(total.issues)} issues"
    )


@app.command("backfill-funding")
def backfill_funding_cmd(
    limit: int = typer.Option(50, help="0 = every eligible contract"),
    min_history_days: int = typer.Option(365),
    workers: int = 12,
    root: Path | None = None,
) -> None:
    """Backfill funding history. Free, no account (§4.7)."""
    from datetime import date as _date
    from datetime import timedelta as _td

    from tradelab.collectors.backfill import backfill_funding_universe, listing_dates

    archive = root or get_settings().archive_root
    assets = listing_dates(limit=limit or None, min_history_days=min_history_days)
    total = backfill_funding_universe(assets, _date.today() - _td(days=1), archive, workers)
    typer.echo(
        f"funding: {total.days_written} days, {total.rows:,} settlements, "
        f"{total.bytes_written / 2**20:.1f} MB, {len(total.issues)} issues"
    )


@app.command("materialise")
def materialise_cmd(
    timeframes: str = typer.Option("1h,4h", help="Comma-separated"),
    limit: int = typer.Option(0, help="0 = every asset with funding"),
    workers: int = 8,
    force: bool = False,
    root: Path | None = None,
) -> None:
    """Derive and persist coarser bars from the 1m archive (§9.6)."""
    from tradelab.orchestration.pooled import funding_universe
    from tradelab.storage.materialise_bars import materialise_universe

    archive = root or get_settings().archive_root
    assets = funding_universe(archive, limit=limit or None)
    tfs = tuple(t.strip() for t in timeframes.split(","))
    typer.echo(f"{len(assets)} assets x {len(tfs)} timeframes")
    done = materialise_universe(archive, assets, tfs, workers=workers, force=force)
    rows = sum(d.rows for d in done)
    typer.echo(f"materialised {len(done)} partitions, {rows:,} bars")


@app.command()
def quality(
    kinds: str = typer.Option("ohlcv,funding", help="Comma-separated"),
    worst: int = typer.Option(10, help="Least complete series to list"),
    record: bool = typer.Option(False, help="Persist gaps as data_quality_issues"),
    root: Path | None = None,
) -> None:
    """Coverage report over the archive. Phase 1 exit criterion (§17)."""
    from tradelab.storage.quality import record_gaps, scan_coverage

    archive = root or get_settings().archive_root
    report = scan_coverage(archive, tuple(k.strip() for k in kinds.split(",")))
    typer.echo(report.summary())

    incomplete = [c for c in report.coverage if c.missing]
    typer.echo(f"\n{len(incomplete)} series with gaps, of {len(report.coverage)}")
    for c in report.worst(worst):
        if not c.missing:
            break
        typer.echo(
            f"  {c.asset:<16}{c.kind:<10}{c.completeness:>7.1%}  "
            f"{len(c.missing):>5} missing, longest run {c.longest_gap}  "
            f"({c.first} to {c.last})"
        )

    if record:
        from tradelab.registry.db import session as db_session

        with db_session() as session:
            written = record_gaps(report, session)
        typer.echo(f"\nrecorded {written} gap issues; a gap only visible by rerunning "
                   f"this report is discoverable, not documented (§4.2)")


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
