"""CLI entry points for test-farm."""

import asyncio
from pathlib import Path

import typer

from test_farm.invocation import execute_invocation
from test_farm.runtime.networking import parse_reachable_service_endpoint
from test_farm.runtime.preparation import (
    RuntimePreparationError,
    prepare_router_runtime,
    prepare_toy_client_runtime,
    prepare_toy_update_server_runtime,
)
from test_farm.scenario import ScenarioFileError, load_scenario_file
import logging
import sys

def configure_logging(verbose: bool = False) -> None:
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(levelname).1s%(asctime)s %(process)d %(filename)s:%(lineno)d] %(message)s",
        datefmt="%m%d %H:%M:%S",
        force=True,
    )


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
    keep_containers: bool = typer.Option(
        False,
        "--keep-containers",
        help="Preserve stopped runtime artifacts for debugging instead of tearing them down.",
    ),
    results_dir: Path = typer.Option(
        Path("results"), "--results-dir", file_okay=False, resolve_path=True
    )
) -> None:
    """Run the current baseline invocation slice."""
    configure_logging(verbose=True)
    try:
        parse_reachable_service_endpoint(controller_bind_address)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    try:
        scenario = load_scenario_file(scenario_file)
    except ScenarioFileError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    result_file, invocation_status = asyncio.run(
        execute_invocation(
            scenario=scenario,
            controller_bind_address=controller_bind_address,
            results_dir=results_dir,
            keep_containers=keep_containers,
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
        result_client = prepare_toy_client_runtime(force_rebuild=force)
        result_server = prepare_toy_update_server_runtime(force_rebuild=force)
        result_router = prepare_router_runtime(force_rebuild=force)
    except RuntimePreparationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error

    if result_client.created:
        if force:
            typer.echo(f"Rebuilt baseline toy-client runtime image {result_client.image_tag}.")
        else:
            typer.echo(
                f"Prepared baseline toy-client runtime image {result_client.image_tag}."
            )
    else:
        typer.echo(
            f"Baseline toy-client runtime image {result_client.image_tag} already exists. "
            "Freshness is not checked; rerun with --force to rebuild it."
        )

    if result_server.created:
        if force:
            typer.echo(
                f"Rebuilt baseline toy-update server runtime image {result_server.image_tag}."
            )
        else:
            typer.echo(
                f"Prepared baseline toy-update server runtime image {result_server.image_tag}."
            )
    else:
        typer.echo(
            f"Baseline toy-update server runtime image {result_server.image_tag} already exists. "
            "Freshness is not checked; rerun with --force to rebuild it."
        )

    if result_router.created:
        if force:
            typer.echo(f"Rebuilt baseline router runtime image {result_router.image_tag}.")
        else:
            typer.echo(f"Prepared baseline router runtime image {result_router.image_tag}.")
    else:
        typer.echo(
            f"Baseline router runtime image {result_router.image_tag} already exists. "
            "Freshness is not checked; rerun with --force to rebuild it."
        )


def main() -> None:
    """Run the CLI application."""
    app()
