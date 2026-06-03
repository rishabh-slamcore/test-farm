"""CLI contract tests."""

from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from test_farm.cli import app
from test_farm.runtime.preparation import RuntimePreparationError, RuntimePreparationResult
from test_farm.scenario import Scenario, load_scenario_file


def _assert_logged_messages(stderr: str, expected_messages: list[str]) -> None:
    for message in expected_messages:
        assert message in stderr


@pytest.mark.parametrize(
    ("scenario_contents", "expected_error"),
    [
        ("not: [valid yaml\n", "is not valid YAML"),
        (
            "- client_count: 1\n",
            "must contain a mapping with only client_count, receipt_timeout_seconds, and optional network_impairment",
        ),
        ("{}\n", "is missing required field client_count"),
        ("client_count: 1\n", "is missing required field receipt_timeout_seconds"),
        (
            "client_count: 1\nreceipt_timeout_seconds: 0\nmode: baseline\n",
            "contains unknown fields: mode",
        ),
        (
            "client_count: 0\nreceipt_timeout_seconds: 0\n",
            "must set client_count to a positive integer",
        ),
        (
            "client_count: true\nreceipt_timeout_seconds: 0\n",
            "must set client_count to a positive integer",
        ),
        (
            "client_count: 1\nreceipt_timeout_seconds: true\n",
            "must set receipt_timeout_seconds to a non-negative number",
        ),
        (
            "client_count: 1\nreceipt_timeout_seconds: 0\nnetwork_impairment: []\n",
            "must set network_impairment to a mapping",
        ),
        (
            (
                "client_count: 1\n"
                "receipt_timeout_seconds: 0\n"
                "network_impairment:\n"
                "  jitter: 20ms\n"
            ),
            "contains unknown network_impairment fields: jitter",
        ),
        (
            (
                "client_count: 1\n"
                "receipt_timeout_seconds: 0\n"
                "network_impairment:\n"
                "  delay: 100\n"
            ),
            "must set network_impairment.delay to a duration like 100ms",
        ),
        (
            (
                "client_count: 1\n"
                "receipt_timeout_seconds: 0\n"
                "network_impairment:\n"
                "  loss: 101\n"
            ),
            "must set network_impairment.loss to a percentage between 0 and 100",
        ),
    ],
)
def test_run_exits_with_code_2_for_invalid_scenario_files(
    tmp_path: Path,
    reachable_bind_address: str,
    scenario_contents: str,
    expected_error: str,
) -> None:
    runner = CliRunner()
    scenario_file = tmp_path / "invalid.yaml"
    scenario_file.write_text(scenario_contents, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            reachable_bind_address,
        ],
    )

    assert result.exit_code == 2
    assert expected_error in result.stderr
    assert str(scenario_file) in result.stderr
    assert not (tmp_path / "results").exists()


def test_load_scenario_file_defaults_network_impairment_to_none(tmp_path: Path) -> None:
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text(
        "client_count: 1\nreceipt_timeout_seconds: 0\n",
        encoding="utf-8",
    )

    scenario = load_scenario_file(scenario_file)

    assert scenario.network_impairment is None


def test_load_scenario_file_parses_supported_router_network_impairment(
    tmp_path: Path,
) -> None:
    scenario_file = tmp_path / "impaired.yaml"
    scenario_file.write_text(
        (
            "client_count: 2\n"
            "receipt_timeout_seconds: 1.5\n"
            "network_impairment:\n"
            "  delay: 100ms\n"
            "  loss: 5\n"
            "  bandwidth_limit: 1mbit\n"
        ),
        encoding="utf-8",
    )

    scenario = load_scenario_file(scenario_file)

    assert scenario.network_impairment is not None
    assert scenario.network_impairment.delay == "100ms"
    assert scenario.network_impairment.loss == 5.0
    assert scenario.network_impairment.bandwidth_limit == "1mbit"


def test_prepare_runtime_reports_when_image_is_already_prepared(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_force_values: list[bool] = []

    def _prepare_toy_client_runtime(*, force_rebuild: bool) -> RuntimePreparationResult:
        observed_force_values.append(force_rebuild)
        return RuntimePreparationResult(
            image_tag="test-farm/toy-client-runtime:latest",
            created=False,
        )

    monkeypatch.setattr(
        "test_farm.cli.prepare_toy_client_runtime",
        _prepare_toy_client_runtime,
    )
    monkeypatch.setattr(
        "test_farm.cli.prepare_toy_update_server_runtime",
        lambda *, force_rebuild: RuntimePreparationResult(
            image_tag="test-farm/toy-update-server-runtime:latest",
            created=False,
        ),
    )
    monkeypatch.setattr(
        "test_farm.cli.prepare_router_runtime",
        lambda *, force_rebuild: RuntimePreparationResult(
            image_tag="test-farm/router-runtime:latest",
            created=False,
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["prepare-runtime"])

    assert result.exit_code == 0
    assert observed_force_values == [False]
    assert result.stdout == ""
    _assert_logged_messages(
        result.stderr,
        [
            "Baseline toy-client runtime image test-farm/toy-client-runtime:latest already exists. "
            "Freshness is not checked; rerun with --force to rebuild it.",
            "Baseline toy-update server runtime image test-farm/toy-update-server-runtime:latest already exists. "
            "Freshness is not checked; rerun with --force to rebuild it.",
            "Baseline router runtime image test-farm/router-runtime:latest already exists. "
            "Freshness is not checked; rerun with --force to rebuild it.",
        ],
    )


def test_prepare_runtime_exits_with_code_1_when_runtime_preparation_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    def _raise_error(*, force_rebuild: bool) -> RuntimePreparationResult:
        del force_rebuild
        raise RuntimePreparationError(
            "Docker CLI is required to prepare the toy-client runtime."
        )

    monkeypatch.setattr("test_farm.cli.prepare_toy_client_runtime", _raise_error)
    runner = CliRunner()

    result = runner.invoke(app, ["prepare-runtime"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Docker CLI is required to prepare the toy-client runtime." in result.stderr


def test_prepare_runtime_rebuilds_when_forced(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_force_values: list[bool] = []

    monkeypatch.setattr(
        "test_farm.cli.prepare_toy_client_runtime",
        lambda *, force_rebuild: RuntimePreparationResult(
            image_tag="test-farm/toy-client-runtime:latest",
            created=True,
        ),
    )
    monkeypatch.setattr(
        "test_farm.cli.prepare_toy_update_server_runtime",
        lambda *, force_rebuild: RuntimePreparationResult(
            image_tag="test-farm/toy-update-server-runtime:latest",
            created=True,
        ),
    )
    monkeypatch.setattr(
        "test_farm.cli.prepare_router_runtime",
        lambda *, force_rebuild: RuntimePreparationResult(
            image_tag="test-farm/router-runtime:latest",
            created=True,
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["prepare-runtime", "--force"])

    assert result.exit_code == 0
    assert result.stdout == ""
    _assert_logged_messages(
        result.stderr,
        [
            "Rebuilt baseline toy-client runtime image test-farm/toy-client-runtime:latest.",
            "Rebuilt baseline toy-update server runtime image test-farm/toy-update-server-runtime:latest.",
            "Rebuilt baseline router runtime image test-farm/router-runtime:latest.",
        ],
    )


def test_run_exits_with_code_2_for_loopback_controller_bind_address(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    def _unexpected_execute_invocation(**kwargs: object) -> object:
        del kwargs
        raise AssertionError("execute_invocation should not be called")

    monkeypatch.setattr(
        "test_farm.cli.execute_invocation",
        _unexpected_execute_invocation,
    )
    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text(
        "client_count: 1\nreceipt_timeout_seconds: 0\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
        ],
    )

    assert result.exit_code == 2
    assert (
        "Controller bind address must use a concrete non-loopback IPv4 address so "
        "runtime-isolated clients can reach the host-side services." in result.stderr
    )


@pytest.mark.parametrize("bind_address", ["0.0.0.0:8080", "localhost:8080"])
def test_run_exits_with_code_2_for_unreachable_controller_bind_address(
    tmp_path: Path, monkeypatch: MonkeyPatch, bind_address: str
) -> None:
    def _unexpected_execute_invocation(**kwargs: object) -> object:
        del kwargs
        raise AssertionError("execute_invocation should not be called")

    monkeypatch.setattr(
        "test_farm.cli.execute_invocation",
        _unexpected_execute_invocation,
    )
    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text(
        "client_count: 1\nreceipt_timeout_seconds: 0\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            bind_address,
        ],
    )

    assert result.exit_code == 2
    if bind_address == "0.0.0.0:8080":
        assert (
            "Controller bind address must use a concrete non-loopback IPv4 address so "
            "runtime-isolated clients can reach the host-side services." in result.stderr
        )
        return

    assert "Controller bind address must use an IPv4 address, got localhost." in result.stderr
