"""CLI entry point for the real-device Disruptor utility."""

import logging
import sys
from pathlib import Path

import typer

from test_farm.disruptor.models import TCExecutionError
from test_farm.disruptor.planning import (
    apply_disruptor_tc_plan,
    build_disruptor_tc_plan,
    discover_aware_devices,
    render_disruptor_dry_run,
)
from test_farm.scenario import (
    DisruptorScenario,
    DisruptorScenarioFileError,
    load_disruptor_scenario_file,
)

logger = logging.getLogger(__name__)

app = typer.Typer(help="Apply network impairment to discovered Slamcore Aware devices.")


def configure_logging(verbose: bool = False) -> None:
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(levelname).1s%(asctime)s %(process)d %(filename)s:%(lineno)d] %(message)s",
        datefmt="%m%d %H:%M:%S",
        force=True,
    )


@app.command()
def run(
    scenario_file: Path = typer.Argument(
        ..., dir_okay=False, readable=True, resolve_path=True
    ),
    interface_name: str = typer.Option(..., "--interface", help="NIC to apply scenario upon"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Apply a Disruptor Scenario File to real devices."""
    configure_logging(verbose=True)
    try:
        scenario: DisruptorScenario = load_disruptor_scenario_file(scenario_file)
    except DisruptorScenarioFileError as error:
        logger.error(str(error))
        raise typer.Exit(code=2) from error

    plan = build_disruptor_tc_plan(
        interface_name=interface_name,
        devices=discover_aware_devices(),
        scenario=scenario,
    )
    if dry_run:
        typer.echo(render_disruptor_dry_run(plan), nl=False)
        return

    try:
        logger.info("Disruptor starting network impairment.")
        apply_disruptor_tc_plan(plan)
    except KeyboardInterrupt:
        logger.info("Interrupted; Disruptor tc state cleaned up.")
        raise typer.Exit(code=0) from None
    except TCExecutionError as error:
        logger.error(str(error))
        raise typer.Exit(code=1) from error


def main() -> None:
    """Run the Disruptor CLI application."""
    app()
