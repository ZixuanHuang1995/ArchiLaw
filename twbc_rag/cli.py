"""Command line. `twbc ingest` and `python -m twbc_rag ingest` both work."""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from twbc_rag.db.session import session_scope
from twbc_rag.ingest.pipeline import ingest_configured
from twbc_rag.ingest.stats import collect_stats, format_stats
from twbc_rag.paths import DEFAULT_CONFIG, DEFAULT_JSONL_DIR

app = typer.Typer(no_args_is_help=True, help="Taiwan building-code corpus tools.")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        force=True,
    )


@app.command()
def ingest(
    pcodes: str | None = typer.Option(
        None,
        help="Comma-separated pcodes. Defaults to every entry in the catalog YAML.",
    ),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Pcode catalog."),
    refresh: bool = typer.Option(False, help="Redownload MOJ zips instead of using data/raw."),
    force: bool = typer.Option(
        False,
        help="Reparse a version even when LawModifiedDate is already stored.",
    ),
    no_pages: bool = typer.Option(False, help="Skip LawAll.aspx attachment pages."),
    jsonl_dir: Path = typer.Option(
        DEFAULT_JSONL_DIR, help="Where articles.jsonl and clauses.jsonl go."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Load configured laws into Postgres and write a JSONL snapshot."""
    _setup_logging(verbose)
    selected = [part.strip() for part in pcodes.split(",") if part.strip()] if pcodes else None
    report = ingest_configured(
        pcodes=selected,
        config_path=config,
        refresh=refresh,
        force=force,
        fetch_pages=not no_pages,
        jsonl_dir=jsonl_dir,
    )
    for note in report.source_notes:
        typer.echo(note)
    for item in report.results:
        typer.echo(f"{item.status:10} {item.pcode}  {item.name}  {item.detail}")
    for warning in report.warnings:
        typer.echo(f"warning: {warning}")
    typer.echo("")
    typer.echo(format_stats(report.stats))
    if report.missing_pcodes:
        typer.echo(f"missing pcodes: {', '.join(report.missing_pcodes)}", err=True)
        raise typer.Exit(code=1)


@app.command("stats")
def stats_command() -> None:
    """Print article / clause / cross-reference counts for the latest version of each law."""
    with session_scope() as session:
        typer.echo(format_stats(collect_stats(session)))
