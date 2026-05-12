"""CLI tests for baseline invocation behavior."""

import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from test_farm.cli import app
from test_farm.controller import ClientOutcome as ControllerClientOutcome
from test_farm.models import DEFAULT_BUNDLE, Bundle, ClientStatus
from test_farm.runtime.preparation import RuntimePreparationError, RuntimePreparationResult


@pytest.mark.parametrize(
    ("scenario_contents", "expected_error"),
    [
        ("not: [valid yaml\n", "is not valid YAML"),
        ("- client_count: 1\n", "must contain a mapping with only client_count"),
        ("{}\n", "is missing required field client_count"),
        ("client_count: 1\nmode: baseline\n", "contains unknown fields: mode"),
        ("client_count: 0\n", "must set client_count to a positive integer"),
        ("client_count: true\n", "must set client_count to a positive integer"),
    ],
)
def test_run_exits_with_code_2_for_invalid_scenario_files(
    tmp_path: Path,
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
            "127.0.0.1:8080",
        ],
    )

    assert result.exit_code == 2
    assert expected_error in result.stderr
    assert str(scenario_file) in result.stderr
    assert not (tmp_path / "results").exists()


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
    runner = CliRunner()

    result = runner.invoke(app, ["prepare-runtime"])

    assert result.exit_code == 0
    assert observed_force_values == [False]
    assert (
        result.output
        == "Baseline toy-client runtime image test-farm/toy-client-runtime:latest already exists. "
        "Freshness is not checked; rerun with --force to rebuild it.\n"
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
    assert result.stderr == "Docker CLI is required to prepare the toy-client runtime.\n"


def test_prepare_runtime_rebuilds_when_forced(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_force_values: list[bool] = []

    def _prepare_toy_client_runtime(*, force_rebuild: bool) -> RuntimePreparationResult:
        observed_force_values.append(force_rebuild)
        return RuntimePreparationResult(
            image_tag="test-farm/toy-client-runtime:latest",
            created=True,
        )

    monkeypatch.setattr(
        "test_farm.cli.prepare_toy_client_runtime",
        _prepare_toy_client_runtime,
    )
    runner = CliRunner()

    result = runner.invoke(app, ["prepare-runtime", "--force"])

    assert result.exit_code == 0
    assert observed_force_values == [True]
    assert (
        result.output
        == "Rebuilt baseline toy-client runtime image test-farm/toy-client-runtime:latest.\n"
    )


def test_run_writes_timed_out_result_file_for_one_client(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _patch_toy_client(monkeypatch, client_status=ClientStatus.SUCCESS)
    _patch_controller_server(monkeypatch, test_client_success=False)
    _patch_update_server(monkeypatch, manifest_bundle=DEFAULT_BUNDLE)
    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
        ],
    )
    result_file = tmp_path / "results" / "001" / "result.json"
    payload = json.loads(result_file.read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert result_file.exists()
    assert payload["invocation_instance"] == 1
    assert payload["scenario_file"] == str(scenario_file)
    assert payload["invocation_status"] == "failed"
    assert payload["expected_bundle"] == DEFAULT_BUNDLE.to_payload()
    assert payload["invocation_error"] is None
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "timed_out",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": "No receipt received before timeout.",
        }
    ]
    assert "started_at" in payload
    assert "finished_at" in payload


def test_run_increments_invocation_instance_from_existing_result_files(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_toy_client(monkeypatch, client_status=ClientStatus.SUCCESS)
    _patch_controller_server(monkeypatch, test_client_success=False)
    _patch_update_server(monkeypatch, manifest_bundle=DEFAULT_BUNDLE)
    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    results_dir = tmp_path / "results"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
    results_dir.mkdir()
    existing_invocation_dir = results_dir / "002"
    existing_invocation_dir.mkdir()
    (existing_invocation_dir / "result.json").write_text("{}\n", encoding="utf-8")
    (results_dir / "notes.txt").write_text("ignore me\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
        ],
    )

    assert result.exit_code == 1
    assert (results_dir / "003" / "result.json").exists()


def test_run_accepts_one_valid_receipt_and_writes_success_result(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    observed_bind_addresses: list[str] = []
    observed_expected_bundles: list[Bundle] = []
    _patch_controller_server(
        monkeypatch,
        test_client_success=True,
        observed_bind_addresses=observed_bind_addresses,
        observed_expected_bundles=observed_expected_bundles,
    )
    _patch_update_server(monkeypatch, manifest_bundle=DEFAULT_BUNDLE)
    _patch_toy_client(monkeypatch, client_status=ClientStatus.SUCCESS)

    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
            "--receipt-timeout-seconds",
            "2",
        ],
    )

    result_file = tmp_path / "results" / "001" / "result.json"
    payload = json.loads(result_file.read_text(encoding="utf-8"))

    assert observed_bind_addresses == ["127.0.0.1:8080"]
    assert observed_expected_bundles == [DEFAULT_BUNDLE]
    assert result.exit_code == 0
    assert payload["invocation_status"] == "success"
    assert payload["expected_bundle"] == DEFAULT_BUNDLE.to_payload()
    assert payload["invocation_error"] is None
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "success",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": None,
        }
    ]


def test_run_accepts_fractional_receipt_timeout_seconds(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    observed_timeouts: list[float] = []
    _patch_controller_server(
        monkeypatch,
        test_client_success=True,
        observed_timeouts=observed_timeouts,
    )
    _patch_update_server(monkeypatch, manifest_bundle=DEFAULT_BUNDLE)
    _patch_toy_client(monkeypatch, client_status=ClientStatus.SUCCESS)

    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
            "--receipt-timeout-seconds",
            "0.5",
        ],
    )

    assert result.exit_code == 0
    assert observed_timeouts == [0.5]


def test_run_starts_one_toy_client_per_expected_client_and_records_every_success(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    observed_environments: list[dict[str, str]] = []
    _patch_controller_server(monkeypatch, test_client_success=True)
    _patch_update_server(monkeypatch, manifest_bundle=DEFAULT_BUNDLE)
    _patch_toy_client(
        monkeypatch,
        client_status=ClientStatus.SUCCESS,
        observed_environments=observed_environments,
    )

    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 2\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
            "--receipt-timeout-seconds",
            "2",
        ],
    )

    result_file = tmp_path / "results" / "001" / "result.json"
    payload = json.loads(result_file.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert [environment["TEST_FARM_CLIENT_ID"] for environment in observed_environments] == [
        "client-001",
        "client-002",
    ]
    assert payload["invocation_status"] == "success"
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "success",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": None,
        },
        {
            "client_id": "client-002",
            "client_status": "success",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": None,
        },
    ]


def test_run_fails_without_hiding_successful_clients_when_a_receipt_is_missing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _patch_controller_server(
        monkeypatch,
        test_client_success=False,
        client_outcomes={
            "client-001": _controller_client_outcome(
                client_id="client-001",
                client_status=ClientStatus.SUCCESS,
            ),
            "client-002": _controller_client_outcome(
                client_id="client-002",
                client_status=ClientStatus.DOWNLOAD_FAILED,
                error_detail="bundle download failed",
            ),
        },
    )
    _patch_update_server(monkeypatch, manifest_bundle=DEFAULT_BUNDLE)
    _patch_toy_client(monkeypatch, client_status=ClientStatus.SUCCESS)

    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 2\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
            "--receipt-timeout-seconds",
            "2",
        ],
    )

    result_file = tmp_path / "results" / "001" / "result.json"
    payload = json.loads(result_file.read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert payload["invocation_status"] == "failed"
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "success",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": None,
        },
        {
            "client_id": "client-002",
            "client_status": "download_failed",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": "bundle download failed",
        },
    ]


def test_run_uses_update_server_manifest_bundle_for_controller_and_result_file(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    manifest_bundle = Bundle(
        bundle_id="baseline",
        byte_count=123,
        checksum="derived-from-update-server",
    )
    observed_expected_bundles: list[Bundle] = []

    _patch_controller_server(
        monkeypatch,
        test_client_success=True,
        observed_expected_bundles=observed_expected_bundles,
    )
    _patch_update_server(monkeypatch, manifest_bundle=manifest_bundle)
    _patch_toy_client(monkeypatch, client_status=ClientStatus.SUCCESS)

    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
            "--receipt-timeout-seconds",
            "2",
        ],
    )

    result_file = tmp_path / "results" / "001" / "result.json"
    payload = json.loads(result_file.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert observed_expected_bundles == [manifest_bundle]
    assert payload["expected_bundle"] == manifest_bundle.to_payload()
    assert payload["invocation_error"] is None
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "success",
            "bundle_id": manifest_bundle.bundle_id,
            "error_detail": None,
        }
    ]


def test_run_writes_failed_result_file_when_expected_bundle_fetch_raises_error(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    observed_controller_entries: list[str] = []
    _patch_controller_server(
        monkeypatch,
        test_client_success=True,
        observed_entries=observed_controller_entries,
    )
    _patch_toy_client(monkeypatch, client_status=ClientStatus.SUCCESS)
    _patch_update_server(
        monkeypatch,
        manifest_error=RuntimeError("Could not fetch expected bundle manifest."),
    )

    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
        ],
    )
    result_file = tmp_path / "results" / "001" / "result.json"
    payload = json.loads(result_file.read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert result_file.exists()
    assert payload["invocation_status"] == "failed"
    assert payload["expected_bundle"] is None
    assert payload["invocation_error"] == {
        "stage": "manifest_fetch",
        "detail": "Could not fetch expected bundle manifest.",
    }
    assert payload["clients"] == []
    assert "started_at" in payload
    assert "finished_at" in payload
    assert observed_controller_entries == []
    assert result.output == f"Invocation failed. Result written to {result_file}.\n"
    assert "Could not fetch expected bundle manifest." not in result.output


def test_run_includes_reported_bundle_only_for_checksum_mismatch_outcomes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    reported_bundle = Bundle(
        bundle_id=DEFAULT_BUNDLE.bundle_id,
        byte_count=DEFAULT_BUNDLE.byte_count,
        checksum="mismatched-checksum",
    )
    _patch_controller_server(
        monkeypatch,
        test_client_success=False,
        client_outcomes={
            "client-001": _controller_client_outcome(
                client_id="client-001",
                client_status=ClientStatus.CHECKSUM_MISMATCH,
                error_detail="Receipt checksum did not match the expected bundle.",
                reported_bundle=reported_bundle,
            )
        },
    )
    _patch_toy_client(monkeypatch, client_status=ClientStatus.SUCCESS)
    _patch_update_server(monkeypatch, manifest_bundle=DEFAULT_BUNDLE)

    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
            "--receipt-timeout-seconds",
            "2",
        ],
    )

    result_file = tmp_path / "results" / "001" / "result.json"
    payload = json.loads(result_file.read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "checksum_mismatch",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": "Receipt checksum did not match the expected bundle.",
            "reported_bundle": reported_bundle.to_payload(),
        }
    ]


def _patch_controller_server(
    monkeypatch: MonkeyPatch,
    *,
    test_client_success: bool,
    observed_bind_addresses: list[str] | None = None,
    observed_expected_bundles: list[Bundle] | None = None,
    observed_entries: list[str] | None = None,
    observed_timeouts: list[float] | None = None,
    client_outcomes: dict[str, ControllerClientOutcome] | None = None,
) -> None:
    class FakeControllerServer:
        def __init__(
            self,
            *,
            bind_address: str,
            invocation_instance: int,
            expected_client_ids: tuple[str, ...],
            expected_bundle: Bundle,
        ) -> None:
            del invocation_instance
            if observed_bind_addresses is not None:
                observed_bind_addresses.append(bind_address)
            if observed_expected_bundles is not None:
                observed_expected_bundles.append(expected_bundle)
            self.expected_client_ids = expected_client_ids
            if client_outcomes is not None:
                self.client_outcomes = client_outcomes
            elif test_client_success:
                self.client_outcomes = {
                    client_id: _controller_client_outcome(
                        client_id=client_id,
                        client_status=ClientStatus.SUCCESS,
                    )
                    for client_id in self.expected_client_ids
                }
            else:
                self.client_outcomes = {}

        async def __aenter__(self) -> "FakeControllerServer":
            if observed_entries is not None:
                observed_entries.append("entered")
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            del exc_type
            del exc
            del traceback

        async def wait_for_client_outcomes(self, timeout_seconds: float) -> bool:
            if observed_timeouts is not None:
                observed_timeouts.append(timeout_seconds)
            return len(self.client_outcomes) == len(self.expected_client_ids)

    monkeypatch.setattr(
        "test_farm.invocation.start_controller_server",
        lambda **kwargs: FakeControllerServer(**kwargs),
    )


def _patch_update_server(
    monkeypatch: MonkeyPatch,
    *,
    manifest_bundle: Bundle | None = None,
    manifest_error: Exception | None = None,
) -> None:
    class FakeUpdateServer:
        base_url = "http://update-server.example:8081"

        async def __aenter__(self) -> "FakeUpdateServer":
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            del exc_type
            del exc
            del traceback

    monkeypatch.setattr(
        "test_farm.invocation.start_update_server",
        lambda **kwargs: FakeUpdateServer(),
        raising=False,
    )
    if manifest_error is not None:

        async def _raise_manifest_error(**kwargs: object) -> Bundle:
            del kwargs
            raise manifest_error

        monkeypatch.setattr(
            "test_farm.invocation.fetch_expected_bundle_from_update_server",
            _raise_manifest_error,
            raising=False,
        )
        return

    if manifest_bundle is None:
        raise AssertionError(
            "manifest_bundle must be provided when manifest_error is not set."
        )

    async def _return_manifest_bundle(**kwargs: object) -> Bundle:
        del kwargs
        return manifest_bundle

    monkeypatch.setattr(
        "test_farm.invocation.fetch_expected_bundle_from_update_server",
        _return_manifest_bundle,
        raising=False,
    )


def _patch_toy_client(
    monkeypatch: MonkeyPatch,
    *,
    client_status: ClientStatus,
    error_detail: str | None = None,
    observed_environments: list[dict[str, str]] | None = None,
) -> None:
    async def _run(environment: dict[str, str]) -> int:
        if observed_environments is not None:
            observed_environments.append(dict(environment))
        del error_detail
        return 0 if client_status == ClientStatus.SUCCESS else 1

    monkeypatch.setattr(
        "test_farm.invocation.run_toy_client",
        _run,
        raising=False,
    )


def _controller_client_outcome(
    *,
    client_id: str,
    client_status: ClientStatus,
    error_detail: str | None = None,
    reported_bundle: Bundle | None = None,
) -> ControllerClientOutcome:
    return ControllerClientOutcome(
        client_id=client_id,
        client_status=client_status,
        bundle_id=DEFAULT_BUNDLE.bundle_id,
        error_detail=error_detail,
        reported_bundle=reported_bundle,
    )
