"""Invocation integration tests."""

import asyncio
import json
import socket
from pathlib import Path
from typing import Awaitable, Callable

from pytest import MonkeyPatch

from test_farm.controller import ClientOutcome as ControllerClientOutcome
from test_farm.identifiers import expected_client_ids
from test_farm.invocation import execute_invocation
from test_farm.models import DEFAULT_BUNDLE, Bundle, ClientStatus


def test_execute_invocation_completes_one_client_baseline_with_real_subjects(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Verify orchestration is working correctly by starting real update and controller server.
    Toy client runs and posts valid receipt, with results file then verified.
    """
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "test_farm.invocation.UPDATE_SERVER_BIND_ADDRESS",
        _allocate_bind_address(),
    )

    result_file, invocation_status = asyncio.run(
        execute_invocation(
            scenario_file=scenario_file,
            client_count=1,
            controller_bind_address=_allocate_bind_address(),
            receipt_timeout_seconds=2,
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
        }
    ]


def test_execute_invocation_completes_two_client_baseline_with_real_subjects(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Verify baseline orchestration records one successful outcome per expected client."""
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 2\n", encoding="utf-8")
    monkeypatch.setattr(
        "test_farm.invocation.UPDATE_SERVER_BIND_ADDRESS",
        _allocate_bind_address(),
    )

    result_file, invocation_status = asyncio.run(
        execute_invocation(
            scenario_file=scenario_file,
            client_count=2,
            controller_bind_address=_allocate_bind_address(),
            receipt_timeout_seconds=2,
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


async def _return_manifest_bundle(**kwargs: object) -> Bundle:
    del kwargs
    return DEFAULT_BUNDLE


def test_execute_invocation_cancels_lingering_toy_clients_when_timeout_wins(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
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

    monkeypatch.setattr(
        "test_farm.invocation.start_update_server",
        lambda **kwargs: _FakeUpdateServer(),
        raising=False,
    )
    monkeypatch.setattr(
        "test_farm.invocation.fetch_expected_bundle_from_update_server",
        _return_manifest_bundle,
        raising=False,
    )
    monkeypatch.setattr(
        "test_farm.invocation.start_controller_server",
        lambda **kwargs: _FakeControllerServer(
            wait_for_client_outcomes=_wait_for_client_outcomes
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "test_farm.invocation.run_toy_client",
        _run_toy_client,
        raising=False,
    )

    result_file, invocation_status = asyncio.run(
        execute_invocation(
            scenario_file=scenario_file,
            client_count=1,
            controller_bind_address="127.0.0.1:8080",
            receipt_timeout_seconds=0,
            results_dir=tmp_path / "results",
        )
    )
    payload = json.loads(result_file.read_text(encoding="utf-8"))

    assert invocation_status == "failed"
    assert toy_client_cancelled.is_set()
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": ClientStatus.TIMED_OUT,
            "bundle_id": DEFAULT_BUNDLE.bundle_id,
            "error_detail": "No receipt received before timeout.",
        }
    ]


def test_execute_invocation_cancels_server_wait_task_when_clients_finish_first(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
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
        fake_controller_server.client_outcomes["client-001"] = ControllerClientOutcome(
            client_id="client-001",
            client_status=ClientStatus.SUCCESS,
            bundle_id=DEFAULT_BUNDLE.bundle_id,
            error_detail=None,
        )
        return 0

    monkeypatch.setattr(
        "test_farm.invocation.start_update_server",
        lambda **kwargs: _FakeUpdateServer(),
        raising=False,
    )
    monkeypatch.setattr(
        "test_farm.invocation.fetch_expected_bundle_from_update_server",
        _return_manifest_bundle,
        raising=False,
    )
    monkeypatch.setattr(
        "test_farm.invocation.start_controller_server",
        lambda **kwargs: fake_controller_server,
        raising=False,
    )
    monkeypatch.setattr(
        "test_farm.invocation.run_toy_client",
        _run_toy_client,
        raising=False,
    )

    result_file, invocation_status = asyncio.run(
        execute_invocation(
            scenario_file=scenario_file,
            client_count=1,
            controller_bind_address="127.0.0.1:8080",
            receipt_timeout_seconds=30,
            results_dir=tmp_path / "results",
        )
    )
    payload = json.loads(result_file.read_text(encoding="utf-8"))

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


class _FakeUpdateServer:
    base_url = "http://update-server.example:8081"

    async def __aenter__(self) -> "_FakeUpdateServer":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type
        del exc
        del traceback


class _FakeControllerServer:
    def __init__(
        self,
        *,
        wait_for_client_outcomes: Callable[[float], Awaitable[bool]],
        expected_client_ids: tuple[str, ...] = expected_client_ids(1),
        client_outcomes: dict[str, ControllerClientOutcome] | None = None,
    ) -> None:
        self.expected_client_ids = expected_client_ids
        self.client_outcomes = {} if client_outcomes is None else client_outcomes
        self._wait_for_client_outcomes = wait_for_client_outcomes

    async def __aenter__(self) -> "_FakeControllerServer":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type
        del exc
        del traceback

    async def wait_for_client_outcomes(self, timeout_seconds: float) -> bool:
        return await self._wait_for_client_outcomes(timeout_seconds)


def _allocate_bind_address() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        host, port = server_socket.getsockname()

    return f"{host}:{port}"
