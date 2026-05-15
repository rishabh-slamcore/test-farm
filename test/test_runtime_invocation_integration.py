"""Integration coverage for invocation runners against real controller state."""

import asyncio
import socket
from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path
from shutil import which
from subprocess import run

import pytest

from test_farm.controller import start_controller_server
from test_farm.identifiers import expected_client_ids
from test_farm.models import DEFAULT_BUNDLE, ClientOutcome, ClientStatus
from test_farm.runtime.invocation.docker import DockerInvocationRunner
from test_farm.runtime.invocation.in_process import InProcessInvocationRunner
from test_farm.runtime.invocation_protocol import InvocationRunner
from test_farm.runtime.preparation import PREPARED_TOY_CLIENT_IMAGE_TAG
from test_farm.subjects.update_server import start_update_server

pytestmark = pytest.mark.host_only


def test_in_process_invocation_runner_records_success_client_outcome(
    tmp_path: Path, reachable_bind_address: str, reachable_update_server_bind_address: str
) -> None:

    client_outcomes = asyncio.run(
        _run_successful_runner_session(
            runner=InProcessInvocationRunner(),
            controller_bind_address=reachable_bind_address,
            update_server_bind_address=reachable_update_server_bind_address,
            invocation_dir=tmp_path,
        ),
    )

    assert client_outcomes == {
        "client-001": ClientOutcome(
            client_id="client-001",
            client_status=ClientStatus.SUCCESS,
            bundle_id=DEFAULT_BUNDLE.bundle_id,
            error_detail=None,
        )
    }


def test_in_process_invocation_runner_records_download_failed_client_outcome(
    tmp_path: Path, reachable_bind_address: str, reachable_update_server_bind_address: str
) -> None:
    controller_bind_address = reachable_bind_address

    client_outcomes = asyncio.run(
        _run_download_failed_runner_session(
            runner=InProcessInvocationRunner(),
            controller_bind_address=controller_bind_address,
            update_server_bind_address=reachable_update_server_bind_address,
            invocation_dir=tmp_path,
        )
    )

    assert client_outcomes["client-001"].client_status == ClientStatus.DOWNLOAD_FAILED
    assert client_outcomes["client-001"].bundle_id == DEFAULT_BUNDLE.bundle_id
    assert client_outcomes["client-001"].error_detail is not None
    assert client_outcomes["client-001"].error_detail.startswith(
        f"Request to http://{reachable_update_server_bind_address}/bundles/{DEFAULT_BUNDLE.bundle_id} failed:"
    )


def test_docker_invocation_runner_records_success_client_outcome(
    tmp_path: Path, reachable_bind_address: str, reachable_update_server_bind_address: str
) -> None:
    _skip_if_prepared_docker_runtime_is_unavailable()

    client_outcomes = asyncio.run(
        _run_successful_runner_session(
            runner=DockerInvocationRunner(),
            controller_bind_address=reachable_bind_address,
            update_server_bind_address=reachable_update_server_bind_address,
            invocation_dir=tmp_path,
        )
    )

    assert client_outcomes == {
        "client-001": ClientOutcome(
            client_id="client-001",
            client_status=ClientStatus.SUCCESS,
            bundle_id=DEFAULT_BUNDLE.bundle_id,
            error_detail=None,
        )
    }


def test_docker_invocation_runner_records_download_failed_client_outcome(
    tmp_path: Path, reachable_bind_address: str, reachable_update_server_bind_address: str
) -> None:
    _skip_if_prepared_docker_runtime_is_unavailable()

    client_outcomes = asyncio.run(
        _run_download_failed_runner_session(
            runner=DockerInvocationRunner(),
            controller_bind_address=reachable_bind_address,
            update_server_bind_address=reachable_update_server_bind_address,
            invocation_dir=tmp_path,
        )
    )

    assert client_outcomes["client-001"].client_status == ClientStatus.DOWNLOAD_FAILED
    assert client_outcomes["client-001"].bundle_id == DEFAULT_BUNDLE.bundle_id
    assert client_outcomes["client-001"].error_detail is not None
    assert client_outcomes["client-001"].error_detail.startswith(
        f"Request to http://{reachable_update_server_bind_address}/bundles/{DEFAULT_BUNDLE.bundle_id} failed:"
    )


async def _run_invocation(
    *,
    runner: InvocationRunner,
    controller_bind_address: str,
    update_server_bind_address: str,
    invocation_dir: Path,
    invocation_instance: int,
    run_update_server: bool = True,
) -> dict[str, ClientOutcome]:
    async with AsyncExitStack() as stack:
        update_server = None
        update_server_url = f"http://{update_server_bind_address}"
        if run_update_server:
            update_server = await stack.enter_async_context(
                start_update_server(bind_address=update_server_bind_address)
            )
            update_server_url = update_server.base_url
        async with start_controller_server(
            bind_address=controller_bind_address,
            invocation_instance=invocation_instance,
            expected_client_ids=expected_client_ids(1),
            expected_bundle=DEFAULT_BUNDLE,
        ) as controller_server:
            session = runner.start_session(
                invocation_instance=invocation_instance,
                client_ids=expected_client_ids(1),
                controller_reportback_url=f"http://{controller_bind_address}",
                update_server_url=update_server_url,
                bundle_id=DEFAULT_BUNDLE.bundle_id,
            )
            try:
                all_outcomes_recorded = await controller_server.wait_for_client_outcomes(
                    timeout_seconds=5
                )
                await session.wait_for_subjects()
            finally:
                await session.finalize(
                    invocation_dir=invocation_dir,
                    failed_client_ids=tuple(),
                    keep_containers=False,
                )

    assert all_outcomes_recorded is True
    return controller_server.client_outcomes


async def _run_successful_runner_session(
    *,
    runner: InvocationRunner,
    controller_bind_address: str,
    update_server_bind_address: str,
    invocation_dir: Path,
) -> dict[str, ClientOutcome]:
    return await _run_invocation(
        runner=runner,
        controller_bind_address=controller_bind_address,
        update_server_bind_address=update_server_bind_address,
        invocation_dir=invocation_dir / "success",
        invocation_instance=_invocation_instance_from_bind_address(controller_bind_address),
    )


async def _run_download_failed_runner_session(
    *,
    runner: InvocationRunner,
    controller_bind_address: str,
    update_server_bind_address: str,
    invocation_dir: Path,
) -> dict[str, ClientOutcome]:
    """asf"""
    return await _run_invocation(
        runner=runner,
        controller_bind_address=controller_bind_address,
        update_server_bind_address=update_server_bind_address,
        invocation_dir=invocation_dir / "failure",
        invocation_instance=_invocation_instance_from_bind_address(controller_bind_address),
        run_update_server=False,
    )


def _skip_if_prepared_docker_runtime_is_unavailable() -> None:
    if which("docker") is None:
        pytest.skip("Docker CLI is unavailable on this host.")

    inspect_result = run(
        ["docker", "image", "inspect", PREPARED_TOY_CLIENT_IMAGE_TAG],
        check=False,
        capture_output=True,
        text=True,
    )
    if inspect_result.returncode != 0:
        pytest.skip(
            "Prepared toy-client runtime image is unavailable: "
            f"{inspect_result.stderr.strip() or inspect_result.stdout.strip() or PREPARED_TOY_CLIENT_IMAGE_TAG}"
        )


def _invocation_instance_from_bind_address(bind_address: str) -> int:
    return int(bind_address.rsplit(":", maxsplit=1)[1])
