"""CLI entry points for test-farm."""

from pathlib import Path

import typer

from test_farm.invocation import execute_timed_out_invocation
from test_farm.scenario import ScenarioFileError, load_scenario_file

app = typer.Typer(help="Controlled update-broadcast test harness.")


@app.callback()
def cli() -> None:
    """Top-level CLI group for test-farm commands."""


@app.command()
def run(
    scenario_file: Path = typer.Argument(
        ..., dir_okay=False, readable=True, resolve_path=True
    ),
    controller_bind_address: str = typer.Option(..., "--controller-bind-address"),
    controller_reportback_url: str = typer.Option(..., "--controller-reportback-url"),
    receipt_timeout_seconds: int = typer.Option(0, "--receipt-timeout-seconds", min=0),
    results_dir: Path = typer.Option(
        Path("results"), "--results-dir", file_okay=False, resolve_path=True
    ),
) -> None:
    """Run the thinnest timed-out baseline invocation."""
    del controller_bind_address
    del controller_reportback_url

    try:
        scenario = load_scenario_file(scenario_file)
    except ScenarioFileError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    result_file = execute_timed_out_invocation(
        scenario_file=scenario_file,
        client_count=scenario.client_count,
        receipt_timeout_seconds=receipt_timeout_seconds,
        results_dir=results_dir,
    )
    typer.echo(f"Invocation failed. Result written to {result_file}.")
    raise typer.Exit(code=1)


def main() -> None:
    """Run the CLI application."""
    app()
