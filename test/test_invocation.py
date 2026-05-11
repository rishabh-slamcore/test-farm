"""Invocation integration tests."""

import asyncio
import json
import socket
from pathlib import Path

from pytest import MonkeyPatch

from test_farm.invocation import execute_invocation
from test_farm.models import DEFAULT_BUNDLE


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


def _allocate_bind_address() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        host, port = server_socket.getsockname()

    return f"{host}:{port}"
