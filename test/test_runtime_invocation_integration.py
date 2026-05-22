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
from test_farm.identifiers import expected_client_ids, runtime_container_name
from test_farm.models import DEFAULT_BUNDLE, ClientOutcome, ClientStatus
from test_farm.runtime.invocation.docker import DockerInvocationRunner
from test_farm.runtime.invocation.in_process import InProcessInvocationRunner
from test_farm.runtime.invocation_protocol import InvocationRunner
from test_farm.runtime.preparation import (
    PREPARED_ROUTER_IMAGE_TAG,
    PREPARED_TOY_CLIENT_IMAGE_TAG,
    PREPARED_TOY_UPDATE_SERVER_IMAGE_TAG,
)
from test_farm.subjects.update_server import start_update_server

pytestmark = pytest.mark.host_only


def test_in_process_invocation_runner_records_success_client_outcome(
    tmp_path: Path, reachable_bind_address: str, reachable_update_server_bind_address: str
) -> None:

    client_outcomes = asyncio.run(
        _run_successful_runner_session(
            runner=InProcessInvocationRunner(
                invocation_instance=_invocation_instance_from_bind_address(
                    reachable_bind_address
                )
            ),
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
    client_outcomes = asyncio.run(
        _run_download_failed_runner_session(
            runner=InProcessInvocationRunner(
                invocation_instance=_invocation_instance_from_bind_address(
                    reachable_bind_address
                )
            ),
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


def test_docker_invocation_runner_records_success_client_outcome(
    tmp_path: Path, reachable_bind_address: str, reachable_update_server_bind_address: str
) -> None:
    _skip_if_prepared_docker_runtime_is_unavailable()

    client_outcomes = asyncio.run(
        _run_successful_runner_session(
            runner=DockerInvocationRunner(
                invocation_instance=_invocation_instance_from_bind_address(
                    reachable_bind_address
                )
            ),
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
            runner=DockerInvocationRunner(
                invocation_instance=_invocation_instance_from_bind_address(
                    reachable_bind_address
                )
            ),
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


def test_docker_routed_client_cannot_reach_unrelated_host_endpoint_and_still_reports_success(
    tmp_path: Path, reachable_bind_address: str, reachable_update_server_bind_address: str
) -> None:
    _skip_if_prepared_docker_runtime_is_unavailable()

    blocked_direct_host_access, client_outcomes = asyncio.run(
        _run_routed_client_isolation_probe(
            runner=DockerInvocationRunner(
                invocation_instance=_invocation_instance_from_bind_address(
                    reachable_bind_address
                )
            ),
            controller_bind_address=reachable_bind_address,
            update_server_bind_address=reachable_update_server_bind_address,
            invocation_dir=tmp_path,
        )
    )

    assert blocked_direct_host_access is True
    assert client_outcomes == {
        "client-001": ClientOutcome(
            client_id="client-001",
            client_status=ClientStatus.SUCCESS,
            bundle_id=DEFAULT_BUNDLE.bundle_id,
            error_detail=None,
        )
    }


async def _run_invocation(
    *,
    runner: InvocationRunner,
    controller_bind_address: str,
    update_server_bind_address: str,
    invocation_dir: Path,
    invocation_instance: int,
    run_update_server: bool = True,
) -> dict[str, ClientOutcome]:

    update_server_url = f"http://{update_server_bind_address}"
    if run_update_server:
        update_server_url = await runner.start_update_server(
            bind_address=update_server_bind_address
        )
    async with start_controller_server(
        bind_address=controller_bind_address,
        invocation_instance=invocation_instance,
        expected_client_ids=expected_client_ids(1),
        expected_bundle=DEFAULT_BUNDLE,
    ) as controller_server:
        session = runner.start_session(
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


async def _run_routed_client_isolation_probe(
    *,
    runner: DockerInvocationRunner,
    controller_bind_address: str,
    update_server_bind_address: str,
    invocation_dir: Path,
) -> tuple[bool, dict[str, ClientOutcome]]:
    invocation_instance = _invocation_instance_from_bind_address(controller_bind_address)
    blocked_host, _controller_port_text = controller_bind_address.rsplit(":", maxsplit=1)
    blocked_listener = _create_blocked_listener(host=blocked_host)
    held_client_containers: list[str] = []
    release_client_start_gate = runner._release_client_start_gate
    all_outcomes_recorded = False

    def _hold_client_start_gate(*, container_name: str) -> None:
        held_client_containers.append(container_name)

    setattr(runner, "_release_client_start_gate", _hold_client_start_gate)
    blocked_direct_host_access = False

    try:
        update_server_url = await runner.start_update_server(
            bind_address=update_server_bind_address
        )
        async with start_controller_server(
            bind_address=controller_bind_address,
            invocation_instance=invocation_instance,
            expected_client_ids=expected_client_ids(1),
            expected_bundle=DEFAULT_BUNDLE,
        ) as controller_server:
            session = runner.start_session(
                client_ids=expected_client_ids(1),
                controller_reportback_url=f"http://{controller_bind_address}",
                update_server_url=update_server_url,
                bundle_id=DEFAULT_BUNDLE.bundle_id,
            )
            try:
                assert session.started_client_ids == ("client-001",)
                blocked_direct_host_access = _probe_tcp_connect_from_container(
                    container_name=runtime_container_name(
                        invocation_instance=invocation_instance,
                        client_id="client-001",
                    ),
                    host=blocked_host,
                    port=blocked_listener.getsockname()[1],
                )
                for container_name in held_client_containers:
                    release_client_start_gate(container_name=container_name)
                all_outcomes_recorded = await controller_server.wait_for_client_outcomes(
                    timeout_seconds=5
                )
                await session.wait_for_subjects()
            finally:
                await session.finalize(
                    invocation_dir=invocation_dir / "isolation",
                    failed_client_ids=tuple(),
                    keep_containers=False,
                )
    finally:
        blocked_listener.close()

    assert all_outcomes_recorded is True
    return blocked_direct_host_access, controller_server.client_outcomes


def _skip_if_prepared_docker_runtime_is_unavailable() -> None:
    if which("docker") is None:
        pytest.skip("Docker CLI is unavailable on this host.")

    for image_tag in (
        PREPARED_TOY_CLIENT_IMAGE_TAG,
        PREPARED_TOY_UPDATE_SERVER_IMAGE_TAG,
        PREPARED_ROUTER_IMAGE_TAG,
    ):
        inspect_result = run(
            ["docker", "image", "inspect", image_tag],
            check=False,
            capture_output=True,
            text=True,
        )
        if inspect_result.returncode != 0:
            pytest.skip(
                "Prepared Docker runtime image is unavailable: "
                f"{inspect_result.stderr.strip() or inspect_result.stdout.strip() or image_tag}"
            )


def _invocation_instance_from_bind_address(bind_address: str) -> int:
    return int(bind_address.rsplit(":", maxsplit=1)[1])


def _create_blocked_listener(*, host: str) -> socket.socket:
    blocked_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocked_listener.bind((host, 0))
    blocked_listener.listen()
    return blocked_listener


def _probe_tcp_connect_from_container(*, container_name: str, host: str, port: int) -> bool:
    probe_result = run(
        [
            "docker",
            "exec",
            container_name,
            "python",
            "-c",
            (
                "import socket; "
                f"connection = socket.create_connection(({host!r}, {port}), 1); "
                "connection.close()"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return probe_result.returncode != 0
