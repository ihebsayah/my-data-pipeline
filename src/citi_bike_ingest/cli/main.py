from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from citi_bike_ingest.ingest.orchestrator import IngestionOrchestrator

app = typer.Typer(help="Citi Bike Ingestion CLI")
console = Console()


@app.command()
def ingest(
    month: str = typer.Option("202601", help="Month in YYYYMM format"),
    dry_run: bool = typer.Option(False, help="Dry run mode"),
) -> None:
    """Ingest a single month's Citi Bike data."""
    try:
        orchestrator = IngestionOrchestrator()
        result = orchestrator.ingest_month(month, dry_run=dry_run)

        if dry_run:
            console.print("[yellow]DRY RUN[/yellow]")
            console.print(json.dumps(result, indent=2))
            return

        table = Table(title="Ingestion Results")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        for k, v in result.items():
            if k != "csv_members":
                table.add_row(k, str(v))
        console.print(table)
        console.print(f"\nCSV Members: {result['csv_members']}")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
