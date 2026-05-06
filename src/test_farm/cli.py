"""CLI entry points for test-farm."""

import typer

app = typer.Typer(help="Controlled update-broadcast test harness.")


@app.callback()
def cli() -> None:
    """Top-level CLI group for test-farm commands."""


@app.command()
def run() -> None:
    """Run a placeholder invocation."""
    typer.echo("test-farm run placeholder: invocation execution is not implemented yet.")


def main() -> None:
    """Run the CLI application."""
    app()
