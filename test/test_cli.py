"""CLI tests for the project bootstrap slice."""

from typer.testing import CliRunner

from test_farm.cli import app


def test_run_command_reports_placeholder() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    assert "placeholder" in result.stdout
