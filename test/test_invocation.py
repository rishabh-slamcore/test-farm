"""Invocation behavior tests."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from pytest import MonkeyPatch

from test_farm.bundles import FileBackedBundleSource
from test_farm.identifiers import expected_client_ids
from test_farm.invocation import execute_invocation
from test_farm.models import DEFAULT_BUNDLE, Bundle, ClientOutcome, ClientStatus
from test_farm.runtime.invocation.in_process import InProcessInvocationRunner
from test_farm.runtime.invocation_protocol import (
    InvocationRunner,
    InvocationSession,
    RuntimeSetupError,
)
from test_farm.scenario import Scenario
from test_farm.subjects.toy_client import CLIENT_ID_ENV
from test_farm.subjects.update_server import UpdateServer


@pytest.mark.host_only
def test_execute_invocation_completes_two_client_baseline_with_real_subjects(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    bind_address_factory: Callable[[], str],
) -> None:
    """Verify baseline orchestration records one successful outcome per expected client."""
    scenario = _write_scenario_file(tmp_path, client_count=2)

    result_file, invocation_status = asyncio.run(
        execute_invocation(
            scenario=scenario,
            controller_bind_address=bind_address_factory(),
            results_dir=tmp_path / "results",
        )
    )
    payload = json.loads(result_file.read_text(encoding="utf-8"))

    assert invocation_status == "success"
    assert payload["invocation_status"] == "success"
    assert payload["expected_bundle"] == DEFAULT_BUNDLE.to_payload()
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


def test_execute_invocation_records_success_for_one_expected_client(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    scenario = _write_scenario_file(tmp_path, client_count=1)
    fake_controller_server = _FakeControllerServer(wait_for_client_outcomes=_return_true)

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=DEFAULT_BUNDLE,
    )
    _patch_fake_controller_server(monkeypatch, fake_controller_server)
    _patch_toy_client(monkeypatch, controller_server=fake_controller_server)

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
    )
    payload = _read_payload(result_file)

    assert result_file.exists()
    assert invocation_status == "success"
    assert payload["invocation_status"] == "success"
    assert payload["expected_bundle"] == DEFAULT_BUNDLE.to_payload()
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "success",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": None,
        }
    ]


def test_execute_invocation_uses_fractional_receipt_timeout_seconds_from_scenario(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    observed_timeouts: list[float] = []
    scenario = _write_scenario_file(
        tmp_path,
        client_count=1,
        receipt_timeout_seconds=0.5,
    )
    fake_controller_server = _FakeControllerServer(
        wait_for_client_outcomes=lambda timeout_seconds: _record_wait_timeout(
            observed_timeouts, timeout_seconds
        ),
        client_outcomes={
            "client-001": _controller_client_outcome(
                client_id="client-001",
                client_status=ClientStatus.SUCCESS,
            )
        },
    )

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=DEFAULT_BUNDLE,
    )
    _patch_fake_controller_server(monkeypatch, fake_controller_server)
    _patch_toy_client(monkeypatch, controller_server=fake_controller_server)

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
    )
    payload = _read_payload(result_file)

    assert invocation_status == "success"
    assert observed_timeouts == [0.5]
    assert payload["invocation_status"] == "success"


def test_execute_invocation_records_multi_client_success_payload_and_launches_each_client(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    observed_environments: list[dict[str, str]] = []
    scenario = _write_scenario_file(tmp_path, client_count=2)
    fake_controller_server = _FakeControllerServer(
        wait_for_client_outcomes=_return_true,
        expected_client_ids=expected_client_ids(2),
    )

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=DEFAULT_BUNDLE,
    )
    _patch_fake_controller_server(monkeypatch, fake_controller_server)
    _patch_toy_client(
        monkeypatch,
        controller_server=fake_controller_server,
        observed_environments=observed_environments,
    )

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
    )
    payload = _read_payload(result_file)

    assert invocation_status == "success"
    assert [environment[CLIENT_ID_ENV] for environment in observed_environments] == [
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


def test_execute_invocation_derives_expected_bundle_from_default_bundle_file(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    reachable_bind_address = "192.168.1.10:8080"
    reachable_update_server_bind_address = "192.168.1.10:8081"
    bundle_bytes = b"bundle bytes from host bundle file\n"
    bundle_file = tmp_path / "baseline"
    bundle_file.write_bytes(bundle_bytes)
    expected_bundle = Bundle.from_bytes(
        bundle_id=DEFAULT_BUNDLE.bundle_id,
        bundle_bytes=bundle_bytes,
    )
    observed_expected_bundles: list[Bundle] = []
    scenario = _write_scenario_file(tmp_path, client_count=1)
    fake_controller_server = _FakeControllerServer(wait_for_client_outcomes=_return_true)

    monkeypatch.setattr(
        "test_farm.bundles.DEFAULT_BUNDLE_FILE",
        bundle_file,
        raising=False,
    )

    monkeypatch.setattr(
        "test_farm.runtime.invocation.in_process.UpdateServer",
        lambda bind_address, bundle_source: _FakeUpdateServer(),
    )

    monkeypatch.setattr(
        "test_farm.invocation.derive_update_server_bind_address",
        lambda bind_address: reachable_update_server_bind_address,
        raising=False,
    )
    _patch_fake_controller_server(
        monkeypatch,
        fake_controller_server,
        observed_expected_bundles=observed_expected_bundles,
    )
    _patch_toy_client(
        monkeypatch,
        controller_server=fake_controller_server,
        reported_bundle=expected_bundle,
    )

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
    )
    payload = _read_payload(result_file)

    assert invocation_status == "success"
    assert observed_expected_bundles == [expected_bundle]
    assert payload["expected_bundle"] == expected_bundle.to_payload()
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "success",
            "bundle_id": expected_bundle.bundle_id,
            "error_detail": None,
        }
    ]


def test_execute_invocation_preserves_successful_clients_when_receipts_are_missing_or_failed(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    scenario = _write_scenario_file(tmp_path, client_count=3)
    fake_controller_server = _FakeControllerServer(
        wait_for_client_outcomes=_return_false,
        expected_client_ids=expected_client_ids(3),
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

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=DEFAULT_BUNDLE,
    )
    _patch_fake_controller_server(monkeypatch, fake_controller_server)
    _patch_toy_client(
        monkeypatch,
        controller_server=fake_controller_server,
        auto_report_success_receipt=False,
    )

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
    )
    payload = _read_payload(result_file)

    assert invocation_status == "failed"
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
        {
            "client_id": "client-003",
            "client_status": "timed_out",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": "No receipt received before timeout.",
        },
    ]


# Todo: update test to use real components once non-default bundles are served.
@pytest.mark.skip(reason="Temporarily disabled.")
def test_execute_invocation_uses_update_server_manifest_bundle_for_controller_and_result_file(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    manifest_bundle = Bundle(
        bundle_id="baseline",
        byte_count=123,
        checksum="derived-from-update-server",
    )
    observed_expected_bundles: list[Bundle] = []
    scenario = _write_scenario_file(tmp_path, client_count=1)
    fake_controller_server = _FakeControllerServer(wait_for_client_outcomes=_return_true)

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=manifest_bundle,
    )
    _patch_fake_controller_server(
        monkeypatch,
        fake_controller_server,
        observed_expected_bundles=observed_expected_bundles,
    )
    _patch_toy_client(
        monkeypatch,
        controller_server=fake_controller_server,
        reported_bundle=manifest_bundle,
    )

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
    )
    payload = _read_payload(result_file)

    assert invocation_status == "success"
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


def test_execute_invocation_writes_failed_result_file_when_bundle_file_read_raises_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    observed_controller_entries: list[str] = []
    scenario = _write_scenario_file(tmp_path, client_count=1)
    fake_controller_server = _FakeControllerServer(wait_for_client_outcomes=_return_true)

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_error=RuntimeError("Could not fetch expected bundle manifest."),
    )

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
        invocation_runner=InProcessInvocationRunner(invocation_instance=7),
    )
    payload = _read_payload(result_file)

    assert invocation_status == "failed"
    assert result_file.exists()
    assert payload["invocation_status"] == "failed"
    assert payload["expected_bundle"] is None
    assert payload["invocation_error"] == {
        "stage": "bundle_file_read",
        "detail": "Could not fetch expected bundle manifest.",
    }
    assert payload["clients"] == []
    assert "started_at" in payload
    assert "finished_at" in payload
    assert observed_controller_entries == []


def test_execute_invocation_includes_reported_bundle_only_for_checksum_mismatch_outcomes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    reported_bundle = Bundle(
        bundle_id=DEFAULT_BUNDLE.bundle_id,
        byte_count=DEFAULT_BUNDLE.byte_count,
        checksum="mismatched-checksum",
    )
    scenario = _write_scenario_file(tmp_path, client_count=2)
    fake_controller_server = _FakeControllerServer(
        wait_for_client_outcomes=_return_true,
        expected_client_ids=expected_client_ids(2),
        client_outcomes={
            "client-001": _controller_client_outcome(
                client_id="client-001",
                client_status=ClientStatus.CHECKSUM_MISMATCH,
                error_detail="Receipt checksum did not match the expected bundle.",
                reported_bundle=reported_bundle,
            ),
            "client-002": _controller_client_outcome(
                client_id="client-002",
                client_status=ClientStatus.SUCCESS,
            ),
        },
    )

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=DEFAULT_BUNDLE,
    )
    _patch_fake_controller_server(monkeypatch, fake_controller_server)
    _patch_toy_client(monkeypatch, controller_server=fake_controller_server)

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
    )
    payload = _read_payload(result_file)

    assert invocation_status == "failed"
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "checksum_mismatch",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": "Receipt checksum did not match the expected bundle.",
            "reported_bundle": reported_bundle.to_payload(),
        },
        {
            "client_id": "client-002",
            "client_status": "success",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": None,
        },
    ]
    assert "reported_bundle" not in payload["clients"][1]


def test_execute_invocation_cancels_lingering_toy_clients_when_timeout_wins(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    scenario = _write_scenario_file(
        tmp_path,
        client_count=1,
        receipt_timeout_seconds=0,
    )
    toy_client_started = asyncio.Event()
    toy_client_cancelled = asyncio.Event()

    async def _wait_for_client_outcomes(timeout_seconds: float) -> bool:
        del timeout_seconds
        await toy_client_started.wait()
        return False

    async def _run_toy_client(environment: dict[str, str]) -> int:
        del environment
        toy_client_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            toy_client_cancelled.set()
            raise
        return 0

    _patch_fake_controller_server(
        monkeypatch,
        _FakeControllerServer(wait_for_client_outcomes=_wait_for_client_outcomes),
    )
    _patch_default_invocation_runner(monkeypatch)
    monkeypatch.setattr(
        "test_farm.runtime.invocation.in_process.run_toy_client",
        _run_toy_client,
        raising=False,
    )

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
    )
    payload = _read_payload(result_file)

    assert invocation_status == "failed"
    assert toy_client_cancelled.is_set()
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "timed_out",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": "No receipt received before timeout.",
        }
    ]


def test_execute_invocation_cancels_server_wait_task_when_clients_finish_first(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    scenario = _write_scenario_file(
        tmp_path,
        client_count=1,
        receipt_timeout_seconds=30,
    )
    server_wait_started = asyncio.Event()
    server_wait_cancelled = asyncio.Event()

    async def _wait_for_client_outcomes(timeout_seconds: float) -> bool:
        del timeout_seconds
        server_wait_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            server_wait_cancelled.set()
            raise
        return True

    fake_controller_server = _FakeControllerServer(
        wait_for_client_outcomes=_wait_for_client_outcomes
    )

    async def _run_toy_client(environment: dict[str, str]) -> int:
        del environment
        await server_wait_started.wait()
        fake_controller_server.client_outcomes["client-001"] = _controller_client_outcome(
            client_id="client-001",
            client_status=ClientStatus.SUCCESS,
        )
        return 0

    _patch_fake_controller_server(monkeypatch, fake_controller_server)
    _patch_default_invocation_runner(monkeypatch)
    monkeypatch.setattr(
        "test_farm.runtime.invocation.in_process.run_toy_client",
        _run_toy_client,
        raising=False,
    )

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
    )
    payload = _read_payload(result_file)

    assert invocation_status == "success"
    assert server_wait_cancelled.is_set()
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "success",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": None,
        }
    ]


def test_execute_invocation_writes_top_level_runtime_setup_failure(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    scenario = _write_scenario_file(tmp_path, client_count=1)

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=DEFAULT_BUNDLE,
    )
    _patch_fake_controller_server(
        monkeypatch,
        _FakeControllerServer(wait_for_client_outcomes=_return_true),
    )

    class _SetupFailingRunner:
        async def start_update_server(self, *, bind_address: str) -> str:
            return ""

        def start_session(
            self,
            *,
            client_ids: tuple[str, ...],
            controller_reportback_url: str,
            update_server_url: str,
            bundle_id: str,
        ) -> InvocationSession:
            del client_ids
            del controller_reportback_url
            del update_server_url
            del bundle_id
            raise RuntimeSetupError("Prepared runtime image is missing.")

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
        invocation_runner=_SetupFailingRunner(),
    )
    payload = _read_payload(result_file)

    assert invocation_status == "failed"
    assert payload["invocation_status"] == "failed"
    assert payload["expected_bundle"] == DEFAULT_BUNDLE.to_payload()
    assert payload["invocation_error"] == {
        "stage": "runtime_setup",
        "detail": "Prepared runtime image is missing.",
    }
    assert payload["clients"] == []


def test_execute_invocation_reports_startup_failed_without_overwriting_controller_success(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    scenario = _write_scenario_file(tmp_path, client_count=2)
    observed_wait_timeouts: list[float] = []

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=DEFAULT_BUNDLE,
    )
    _patch_fake_controller_server(
        monkeypatch,
        _FakeControllerServer(
            wait_for_client_outcomes=lambda timeout_seconds: _record_wait_timeout(
                observed_wait_timeouts, timeout_seconds
            ),
            expected_client_ids=expected_client_ids(2),
            client_outcomes={
                "client-002": _controller_client_outcome(
                    client_id="client-002",
                    client_status=ClientStatus.SUCCESS,
                )
            },
        ),
    )

    class _StartupFailingSession:
        started_client_ids = ("client-002",)
        startup_failures = {"client-001": "docker run exited before startup completed"}

        async def wait_for_subjects(self) -> None:
            return None

        async def stop_remaining_subjects(self) -> None:
            return None

        async def finalize(
            self,
            *,
            invocation_dir: Path,
            failed_client_ids: tuple[str, ...],
            keep_containers: bool,
        ) -> str | None:
            del invocation_dir
            del failed_client_ids
            del keep_containers
            return None

    observed_client_id_batches: list[tuple[str, ...]] = []

    class _PartialStartupRunner:
        async def start_update_server(self, *, bind_address: str) -> str:
            return ""

        def start_session(
            self,
            *,
            client_ids: tuple[str, ...],
            controller_reportback_url: str,
            update_server_url: str,
            bundle_id: str,
        ) -> _StartupFailingSession:
            del controller_reportback_url
            del update_server_url
            del bundle_id
            observed_client_id_batches.append(client_ids)
            return _StartupFailingSession()

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
        invocation_runner=_PartialStartupRunner(),
    )
    payload = _read_payload(result_file)

    assert invocation_status == "failed"
    assert observed_client_id_batches == [("client-001", "client-002")]
    assert observed_wait_timeouts == []
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "startup_failed",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": "docker run exited before startup completed",
        },
        {
            "client_id": "client-002",
            "client_status": "success",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": None,
        },
    ]


def test_execute_invocation_marks_invocation_failed_when_runtime_finalization_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    scenario = _write_scenario_file(tmp_path, client_count=1)
    observed_finalization_calls: list[tuple[Path, tuple[str, ...], bool]] = []

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=DEFAULT_BUNDLE,
    )
    _patch_fake_controller_server(
        monkeypatch,
        _FakeControllerServer(
            wait_for_client_outcomes=_return_true,
            client_outcomes={
                "client-001": _controller_client_outcome(
                    client_id="client-001",
                    client_status=ClientStatus.SUCCESS,
                )
            },
        ),
    )

    class _FinalizationFailingSession:
        started_client_ids = ("client-001",)
        startup_failures: dict[str, str] = {}

        async def wait_for_subjects(self) -> None:
            return None

        async def stop_remaining_subjects(self) -> None:
            return None

        async def finalize(
            self,
            *,
            invocation_dir: Path,
            failed_client_ids: tuple[str, ...],
            keep_containers: bool,
        ) -> str | None:
            observed_finalization_calls.append(
                (invocation_dir, failed_client_ids, keep_containers)
            )
            return "Docker failed to remove one or more runtime artifacts."

    class _FinalizationFailingRunner:
        async def start_update_server(self, *, bind_address: str) -> str:
            return ""

        def start_session(
            self,
            *,
            client_ids: tuple[str, ...],
            controller_reportback_url: str,
            update_server_url: str,
            bundle_id: str,
        ) -> _FinalizationFailingSession:
            del client_ids
            del controller_reportback_url
            del update_server_url
            del bundle_id
            return _FinalizationFailingSession()

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
        invocation_runner=_FinalizationFailingRunner(),
    )
    payload = _read_payload(result_file)

    assert invocation_status == "failed"
    assert observed_finalization_calls == [(tmp_path / "results" / "001", tuple(), False)]
    assert payload["invocation_status"] == "failed"
    assert payload["invocation_error"] == {
        "stage": "runtime_finalization",
        "detail": "Docker failed to remove one or more runtime artifacts.",
    }
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "success",
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": None,
        }
    ]


def test_execute_invocation_writes_timed_out_result_file_for_one_client(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    scenario = _write_scenario_file(
        tmp_path,
        client_count=1,
        receipt_timeout_seconds=0,
    )
    fake_controller_server = _FakeControllerServer(wait_for_client_outcomes=_return_false)

    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=DEFAULT_BUNDLE,
    )
    _patch_fake_controller_server(monkeypatch, fake_controller_server)
    _patch_toy_client(
        monkeypatch,
        controller_server=fake_controller_server,
        auto_report_success_receipt=False,
    )

    result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=tmp_path / "results",
    )
    payload = _read_payload(result_file)

    assert invocation_status == "failed"
    assert result_file.exists()
    assert payload["invocation_instance"] == 1
    assert payload["scenario_file"] == str(scenario.scenario_file)
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


def test_execute_invocation_increments_invocation_instance_from_existing_result_files(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    reachable_bind_address: str,
    reachable_update_server_bind_address: str,
) -> None:
    scenario = _write_scenario_file(
        tmp_path,
        client_count=1,
        receipt_timeout_seconds=0,
    )
    results_dir = tmp_path / "results"
    existing_invocation_dir = results_dir / "002"
    existing_invocation_dir.mkdir(parents=True)
    (existing_invocation_dir / "result.json").write_text("{}\n", encoding="utf-8")
    (results_dir / "notes.txt").write_text("ignore me\n", encoding="utf-8")

    fake_controller_server = _FakeControllerServer(wait_for_client_outcomes=_return_false)
    _patch_fake_update_server(
        monkeypatch,
        reachable_update_server_bind_address=reachable_update_server_bind_address,
        manifest_bundle=DEFAULT_BUNDLE,
    )
    _patch_fake_controller_server(monkeypatch, fake_controller_server)
    _patch_toy_client(
        monkeypatch,
        controller_server=fake_controller_server,
        auto_report_success_receipt=False,
    )

    _result_file, invocation_status = execute_invocation_sync(
        scenario=scenario,
        controller_bind_address=reachable_bind_address,
        results_dir=results_dir,
    )

    assert invocation_status == "failed"
    assert (results_dir / "003" / "result.json").exists()


def execute_invocation_sync(
    *,
    scenario: Scenario,
    controller_bind_address: str,
    results_dir: Path,
    invocation_runner: InvocationRunner | None = None,
) -> tuple[Path, str]:
    return asyncio.run(
        execute_invocation(
            scenario=scenario,
            controller_bind_address=controller_bind_address,
            results_dir=results_dir,
            invocation_runner=invocation_runner,
        )
    )


def _write_scenario_file(
    tmp_path: Path,
    *,
    client_count: int,
    receipt_timeout_seconds: float = 2,
) -> Scenario:
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text(
        "client_count: "
        f"{client_count}\n"
        "receipt_timeout_seconds: "
        f"{receipt_timeout_seconds}\n",
        encoding="utf-8",
    )
    return Scenario(
        scenario_file=scenario_file,
        client_count=client_count,
        receipt_timeout_seconds=float(receipt_timeout_seconds),
    )


def _read_payload(result_file: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(result_file.read_text(encoding="utf-8")))


def _patch_fake_update_server(
    monkeypatch: MonkeyPatch,
    *,
    reachable_update_server_bind_address: str,
    manifest_bundle: Bundle | None = None,
    manifest_error: Exception | None = None,
) -> None:
    monkeypatch.setattr(
        "test_farm.invocation.start_update_server",
        lambda **kwargs: _FakeUpdateServer(),
        raising=False,
    )
    monkeypatch.setattr(
        "test_farm.invocation.derive_update_server_bind_address",
        lambda bind_address: reachable_update_server_bind_address,
        raising=False,
    )
    if manifest_error is not None:

        def _raise_bundle_file_error() -> Bundle:
            raise OSError(str(manifest_error))

        monkeypatch.setattr(
            "test_farm.invocation.load_default_bundle",
            _raise_bundle_file_error,
            raising=False,
        )
        return

    if manifest_bundle is None:
        raise AssertionError(
            "manifest_bundle must be provided when manifest_error is not set."
        )

    def _return_configured_bundle() -> Bundle:
        return manifest_bundle

    monkeypatch.setattr(
        "test_farm.invocation.load_default_bundle",
        _return_configured_bundle,
        raising=False,
    )


def _patch_fake_controller_server(
    monkeypatch: MonkeyPatch,
    fake_controller_server: "_FakeControllerServer",
    *,
    observed_bind_addresses: list[str] | None = None,
    observed_expected_bundles: list[Bundle] | None = None,
    observed_entries: list[str] | None = None,
) -> None:
    class _BoundFakeControllerServer:
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
            fake_controller_server.expected_client_ids = expected_client_ids
            if not fake_controller_server.client_outcomes:
                fake_controller_server.client_outcomes = {}

        async def __aenter__(self) -> "_FakeControllerServer":
            if observed_entries is not None:
                observed_entries.append("entered")
            return fake_controller_server

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            del exc_type
            del exc
            del traceback

    monkeypatch.setattr(
        "test_farm.invocation.start_controller_server",
        lambda **kwargs: _BoundFakeControllerServer(**kwargs),
        raising=False,
    )


def _patch_toy_client(
    monkeypatch: MonkeyPatch,
    *,
    controller_server: "_FakeControllerServer",
    observed_environments: list[dict[str, str]] | None = None,
    reported_bundle: Bundle = DEFAULT_BUNDLE,
    auto_report_success_receipt: bool = True,
) -> None:
    async def _run(environment: dict[str, str]) -> int:
        if observed_environments is not None:
            observed_environments.append(dict(environment))
        if auto_report_success_receipt:
            client_id = environment[CLIENT_ID_ENV]
            controller_server.client_outcomes.setdefault(
                client_id,
                _controller_client_outcome(
                    client_id=client_id,
                    client_status=ClientStatus.SUCCESS,
                    bundle_id=reported_bundle.bundle_id,
                ),
            )
        return 0

    monkeypatch.setattr(
        "test_farm.runtime.invocation.in_process.run_toy_client",
        _run,
        raising=False,
    )
    _patch_default_invocation_runner(monkeypatch)


def _patch_default_invocation_runner(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "test_farm.invocation.create_default_invocation_runner",
        lambda invocation_instance: InProcessInvocationRunner(
            invocation_instance=invocation_instance
        ),
        raising=False,
    )


def _controller_client_outcome(
    *,
    client_id: str,
    client_status: ClientStatus,
    error_detail: str | None = None,
    reported_bundle: Bundle | None = None,
    bundle_id: str = DEFAULT_BUNDLE.bundle_id,
) -> ClientOutcome:
    return ClientOutcome(
        client_id=client_id,
        client_status=client_status,
        bundle_id=bundle_id,
        error_detail=error_detail,
        reported_bundle=reported_bundle,
    )


class _FakeUpdateServer:
    base_url = "http://update-server.example:8081"

    async def start(self) -> "_FakeUpdateServer":
        return self

    async def stop(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type
        del exc
        del traceback


class _FakeControllerServer:
    def __init__(
        self,
        *,
        wait_for_client_outcomes: Callable[[float], Awaitable[bool]],
        expected_client_ids: tuple[str, ...] = expected_client_ids(1),
        client_outcomes: dict[str, ClientOutcome] | None = None,
    ) -> None:
        self.expected_client_ids = expected_client_ids
        self.client_outcomes = {} if client_outcomes is None else dict(client_outcomes)
        self._wait_for_client_outcomes = wait_for_client_outcomes

    async def wait_for_client_outcomes(self, timeout_seconds: float) -> bool:
        return await self._wait_for_client_outcomes(timeout_seconds)


async def _return_true(timeout_seconds: float) -> bool:
    del timeout_seconds
    return True


async def _return_false(timeout_seconds: float) -> bool:
    del timeout_seconds
    return False


async def _record_wait_timeout(
    observed_wait_timeouts: list[float], timeout_seconds: float
) -> bool:
    observed_wait_timeouts.append(timeout_seconds)
    return True
