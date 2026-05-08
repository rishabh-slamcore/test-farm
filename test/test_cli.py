"""CLI tests for baseline invocation behavior."""

import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from test_farm.cli import app
from test_farm.models import DEFAULT_BUNDLE, Bundle, ClientStatus
from test_farm.subjects.toy_client import ToyClientResult


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
            "--controller-reportback-url",
            "http://controller.example:8080",
        ],
    )
    result_file = tmp_path / "results" / "result_1.json"
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
            "error_detail": "No verified receipt received before timeout.",
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
    (results_dir / "result_2.json").write_text("{}\n", encoding="utf-8")
    (results_dir / "notes.txt").write_text("ignore me\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
            "--controller-reportback-url",
            "http://controller.example:8080",
        ],
    )

    assert result.exit_code == 1
    assert (results_dir / "result_3.json").exists()


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
            "--controller-reportback-url",
            "http://controller.example:8080",
            "--receipt-timeout-seconds",
            "2",
        ],
    )

    result_file = tmp_path / "results" / "result_1.json"
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
            "--controller-reportback-url",
            "http://controller.example:8080",
            "--receipt-timeout-seconds",
            "2",
        ],
    )

    result_file = tmp_path / "results" / "result_1.json"
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
            "--controller-reportback-url",
            "http://controller.example:8080",
        ],
    )
    result_file = tmp_path / "results" / "result_1.json"
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


def _patch_controller_server(
    monkeypatch: MonkeyPatch,
    *,
    test_client_success: bool,
    observed_bind_addresses: list[str] | None = None,
    observed_expected_bundles: list[Bundle] | None = None,
    observed_entries: list[str] | None = None,
) -> None:
    class FakeControllerServer:
        def __init__(
            self,
            *,
            bind_address: str,
            invocation_instance: int,
            client_id: str,
            expected_bundle: Bundle,
        ) -> None:
            del invocation_instance
            del client_id
            if observed_bind_addresses is not None:
                observed_bind_addresses.append(bind_address)
            if observed_expected_bundles is not None:
                observed_expected_bundles.append(expected_bundle)

        async def __aenter__(self) -> "FakeControllerServer":
            if observed_entries is not None:
                observed_entries.append("entered")
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            del exc_type
            del exc
            del traceback

        async def wait_for_valid_receipt(self, timeout_seconds: int) -> bool:
            del timeout_seconds
            return test_client_success

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
) -> None:
    async def _run(environment: dict[str, str]) -> ToyClientResult:
        return ToyClientResult(
            client_status=client_status,
            bundle_id=environment["TEST_FARM_BUNDLE_ID"],
            error_detail=error_detail,
            exit_code=0 if client_status == ClientStatus.SUCCESS else 1,
            verified_bundle=(
                DEFAULT_BUNDLE if client_status == ClientStatus.SUCCESS else None
            ),
        )

    monkeypatch.setattr(
        "test_farm.invocation.run_toy_client",
        _run,
        raising=False,
    )
