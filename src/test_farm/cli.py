"""CLI entry points for test-farm."""

import asyncio
from pathlib import Path

import typer

from test_farm.invocation import execute_invocation
from test_farm.runtime.preparation import RuntimePreparationError, prepare_toy_client_runtime
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
    receipt_timeout_seconds: float = typer.Option(0.0, "--receipt-timeout-seconds", min=0.0),
    results_dir: Path = typer.Option(
        Path("results"), "--results-dir", file_okay=False, resolve_path=True
    ),
) -> None:
    """Run the current baseline invocation slice."""

    try:
        scenario = load_scenario_file(scenario_file)
    except ScenarioFileError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    result_file, invocation_status = asyncio.run(
        execute_invocation(
            scenario_file=scenario_file,
            client_count=scenario.client_count,
            controller_bind_address=controller_bind_address,
            receipt_timeout_seconds=receipt_timeout_seconds,
            results_dir=results_dir,
        )
    )
    if invocation_status == "success":
        typer.echo(f"Invocation succeeded. Result written to {result_file}.")
        return

    typer.echo(f"Invocation failed. Result written to {result_file}.")
    raise typer.Exit(code=1)


@app.command("prepare-runtime")
def prepare_runtime(
    force: bool = typer.Option(
        False,
        "--force",
        help="Rebuild the runtime image even when the prepared tag already exists.",
    ),
) -> None:
    """Prepare the baseline toy-client runtime image.

    By default this command only checks whether the prepared tag exists. It does not
    verify freshness against the current source tree. Use --force to rebuild.
    """

    try:
        result = prepare_toy_client_runtime(force_rebuild=force)
    except RuntimePreparationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error

    if result.created:
        if force:
            typer.echo(f"Rebuilt baseline toy-client runtime image {result.image_tag}.")
            return
        typer.echo(f"Prepared baseline toy-client runtime image {result.image_tag}.")
        return

    typer.echo(
        f"Baseline toy-client runtime image {result.image_tag} already exists. "
        "Freshness is not checked; rerun with --force to rebuild it."
    )


def main() -> None:
    """Run the CLI application."""
    app()
