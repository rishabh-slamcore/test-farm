"""Runtime invocation boundary tests."""

import asyncio
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest import MonkeyPatch

from test_farm.runtime.invocation.docker import DockerInvocationRunner
from test_farm.runtime.invocation_protocol import RuntimeSetupError


@pytest.mark.usefixtures("docker_available_for_runtime_invocation")
def test_docker_invocation_runner_raises_runtime_setup_error_when_image_inspect_fails() -> (
    None
):
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        return CompletedProcess(args=args, returncode=1, stdout="", stderr="image missing")

    runner = DockerInvocationRunner(invocation_instance=7, command_runner=_command_runner)

    with pytest.raises(
        RuntimeSetupError,
        match=(
            "Prepared runtime image "
            "test-farm/toy-client-runtime:latest is missing. "
            "Run `test-farm prepare-runtime` first."
        ),
    ):
        runner.start_session(
            client_ids=("client-001",),
            controller_reportback_url="http://192.168.1.10:8080",
            update_server_url="http://192.168.1.10:8081",
            bundle_id="baseline",
        )

    assert [args for args, _cwd in observed_calls] == [
        ["docker", "image", "inspect", "test-farm/toy-client-runtime:latest"],
    ]


@pytest.mark.usefixtures("docker_available_for_runtime_invocation")
def test_docker_invocation_runner_attempts_every_expected_client_before_returning_startup_failures() -> (
    None
):
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        if args[:3] == ["docker", "image", "inspect"]:
            return CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if "TEST_FARM_CLIENT_ID=client-001" in args:
            return CompletedProcess(
                args=args,
                returncode=1,
                stdout="",
                stderr="client-001 failed to start",
            )
        return CompletedProcess(args=args, returncode=0, stdout="container-id\n", stderr="")

    runner = DockerInvocationRunner(invocation_instance=7, command_runner=_command_runner)

    session = runner.start_session(
        client_ids=("client-001", "client-002"),
        controller_reportback_url="http://192.168.1.10:8080",
        update_server_url="http://192.168.1.10:8081",
        bundle_id="baseline",
    )
    asyncio.run(session.wait_for_subjects())
    asyncio.run(session.stop_remaining_subjects())

    assert session.started_client_ids == ("client-002",)
    assert dict(session.startup_failures) == {"client-001": "client-001 failed to start"}
    assert [args for args, _cwd in observed_calls] == [
        ["docker", "image", "inspect", "test-farm/toy-client-runtime:latest"],
        ["docker", "network", "create", "test-farm-007"],
        [
            "docker",
            "run",
            "--detach",
            "--name",
            "test-farm-007-client-001",
            "--network",
            "test-farm-007",
            "--env",
            "TEST_FARM_INVOCATION_INSTANCE=7",
            "--env",
            "TEST_FARM_CLIENT_ID=client-001",
            "--env",
            "TEST_FARM_UPDATE_SERVER_URL=http://192.168.1.10:8081",
            "--env",
            "TEST_FARM_CONTROLLER_REPORTBACK_URL=http://192.168.1.10:8080",
            "--env",
            "TEST_FARM_BUNDLE_ID=baseline",
            "test-farm/toy-client-runtime:latest",
        ],
        [
            "docker",
            "run",
            "--detach",
            "--name",
            "test-farm-007-client-002",
            "--network",
            "test-farm-007",
            "--env",
            "TEST_FARM_INVOCATION_INSTANCE=7",
            "--env",
            "TEST_FARM_CLIENT_ID=client-002",
            "--env",
            "TEST_FARM_UPDATE_SERVER_URL=http://192.168.1.10:8081",
            "--env",
            "TEST_FARM_CONTROLLER_REPORTBACK_URL=http://192.168.1.10:8080",
            "--env",
            "TEST_FARM_BUNDLE_ID=baseline",
            "test-farm/toy-client-runtime:latest",
        ],
        ["docker", "wait", "test-farm-007-client-002"],
        ["docker", "stop", "test-farm-007-client-002"],
    ]


@pytest.mark.usefixtures("docker_available_for_runtime_invocation")
def test_docker_invocation_runner_starts_update_server_with_bundle_file_mount(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    bundle_file = tmp_path / "baseline"
    bundle_file.write_bytes(b"bundle bytes from mounted file\n")
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        return CompletedProcess(args=args, returncode=0, stdout="container-id\n", stderr="")

    monkeypatch.setattr(
        "test_farm.runtime.invocation.docker.DEFAULT_BUNDLE_FILE",
        bundle_file,
        raising=False,
    )
    monkeypatch.setattr(
        "test_farm.runtime.invocation.docker.which",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        "test_farm.runtime.invocation.docker._wait_for_http_health",
        lambda url: asyncio.sleep(0),
    )

    runner = DockerInvocationRunner(invocation_instance=7, command_runner=_command_runner)

    update_server_url = asyncio.run(
        runner.start_update_server(bind_address="192.168.1.10:8081")
    )

    assert update_server_url == "http://10.0.7.2:8081"
    assert [args for args, _cwd in observed_calls] == [
        ["docker", "image", "inspect", "test-farm/toy-update-server-runtime:latest"],
        ["docker", "image", "inspect", "test-farm/router-runtime:latest"],
        [
            "docker",
            "network",
            "create",
            "--subnet",
            "10.0.7.0/24",
            "test-farm-007-server-network",
        ],
        [
            "docker",
            "run",
            "--detach",
            "--name",
            "test-farm-007-router",
            "--network",
            "test-farm-007-server-network",
            "--ip",
            "10.0.7.3",
            "--cap-add",
            "NET_ADMIN",
            "--sysctl",
            "net.ipv4.ip_forward=1",
            "test-farm/router-runtime:latest",
        ],
        [
            "docker",
            "run",
            "--detach",
            "--name",
            "test-farm-007-update-server",
            "--network",
            "test-farm-007-server-network",
            "--ip",
            "10.0.7.2",
            "--cap-add",
            "NET_ADMIN",
            "--env",
            "TEST_FARM_UPDATE_SERVER_BIND_ADDRESS=0.0.0.0:8081",
            "--env",
            "TEST_FARM_UPDATE_SERVER_BUNDLE_DIR=/test-farm/bundles",
            "--mount",
            f"type=bind,source={bundle_file},target=/test-farm/bundles/baseline,readonly",
            "test-farm/toy-update-server-runtime:latest",
        ],
    ]


@pytest.mark.usefixtures("docker_available_for_runtime_invocation")
def test_docker_invocation_session_harvests_failed_client_logs_and_removes_runtime_artifacts_by_default(
    tmp_path: Path,
) -> None:
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        if args[:3] == ["docker", "image", "inspect"]:
            return CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args[:3] == ["docker", "logs", "test-farm-007-client-002"]:
            return CompletedProcess(
                args=args,
                returncode=0,
                stdout="client-002 stdout\n",
                stderr="client-002 stderr\n",
            )
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    runner = DockerInvocationRunner(invocation_instance=7, command_runner=_command_runner)
    session = runner.start_session(
        client_ids=("client-001", "client-002"),
        controller_reportback_url="http://192.168.1.10:8080",
        update_server_url="http://192.168.1.10:8081",
        bundle_id="baseline",
    )

    asyncio.run(session.wait_for_subjects())
    finalization_error = asyncio.run(
        session.finalize(
            invocation_dir=tmp_path / "007",
            failed_client_ids=("client-002",),
            keep_containers=False,
        )
    )

    assert finalization_error is None
    assert not (tmp_path / "007" / "client-001.log").exists()
    assert (tmp_path / "007" / "client-002.log").read_text(encoding="utf-8") == (
        "client-002 stdout\nclient-002 stderr\n"
    )
    assert [args for args, _cwd in observed_calls] == [
        ["docker", "image", "inspect", "test-farm/toy-client-runtime:latest"],
        ["docker", "network", "create", "test-farm-007"],
        [
            "docker",
            "run",
            "--detach",
            "--name",
            "test-farm-007-client-001",
            "--network",
            "test-farm-007",
            "--env",
            "TEST_FARM_INVOCATION_INSTANCE=7",
            "--env",
            "TEST_FARM_CLIENT_ID=client-001",
            "--env",
            "TEST_FARM_UPDATE_SERVER_URL=http://192.168.1.10:8081",
            "--env",
            "TEST_FARM_CONTROLLER_REPORTBACK_URL=http://192.168.1.10:8080",
            "--env",
            "TEST_FARM_BUNDLE_ID=baseline",
            "test-farm/toy-client-runtime:latest",
        ],
        [
            "docker",
            "run",
            "--detach",
            "--name",
            "test-farm-007-client-002",
            "--network",
            "test-farm-007",
            "--env",
            "TEST_FARM_INVOCATION_INSTANCE=7",
            "--env",
            "TEST_FARM_CLIENT_ID=client-002",
            "--env",
            "TEST_FARM_UPDATE_SERVER_URL=http://192.168.1.10:8081",
            "--env",
            "TEST_FARM_CONTROLLER_REPORTBACK_URL=http://192.168.1.10:8080",
            "--env",
            "TEST_FARM_BUNDLE_ID=baseline",
            "test-farm/toy-client-runtime:latest",
        ],
        ["docker", "wait", "test-farm-007-client-001"],
        ["docker", "wait", "test-farm-007-client-002"],
        ["docker", "logs", "test-farm-007-client-002"],
        ["docker", "rm", "--force", "test-farm-007-client-001"],
        ["docker", "rm", "--force", "test-farm-007-client-002"],
        ["docker", "network", "rm", "--force", "test-farm-007"],
    ]


@pytest.mark.usefixtures("docker_available_for_runtime_invocation")
def test_docker_invocation_session_preserves_runtime_artifacts_when_keep_containers_is_enabled(
    tmp_path: Path,
) -> None:
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        if args[:3] == ["docker", "image", "inspect"]:
            return CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    runner = DockerInvocationRunner(invocation_instance=7, command_runner=_command_runner)
    session = runner.start_session(
        client_ids=("client-001",),
        controller_reportback_url="http://192.168.1.10:8080",
        update_server_url="http://192.168.1.10:8081",
        bundle_id="baseline",
    )

    asyncio.run(session.wait_for_subjects())
    finalization_error = asyncio.run(
        session.finalize(
            invocation_dir=tmp_path / "007",
            failed_client_ids=tuple(),
            keep_containers=True,
        )
    )

    assert finalization_error is None
    assert not (tmp_path / "007" / "client-001.log").exists()
    assert [args for args, _cwd in observed_calls] == [
        ["docker", "image", "inspect", "test-farm/toy-client-runtime:latest"],
        ["docker", "network", "create", "test-farm-007"],
        [
            "docker",
            "run",
            "--detach",
            "--name",
            "test-farm-007-client-001",
            "--network",
            "test-farm-007",
            "--env",
            "TEST_FARM_INVOCATION_INSTANCE=7",
            "--env",
            "TEST_FARM_CLIENT_ID=client-001",
            "--env",
            "TEST_FARM_UPDATE_SERVER_URL=http://192.168.1.10:8081",
            "--env",
            "TEST_FARM_CONTROLLER_REPORTBACK_URL=http://192.168.1.10:8080",
            "--env",
            "TEST_FARM_BUNDLE_ID=baseline",
            "test-farm/toy-client-runtime:latest",
        ],
        ["docker", "wait", "test-farm-007-client-001"],
    ]


@pytest.mark.usefixtures("docker_available_for_runtime_invocation")
def test_docker_invocation_session_finalization_is_best_effort_and_idempotent(
    tmp_path: Path,
) -> None:
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        if args[:3] == ["docker", "image", "inspect"]:
            return CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args == ["docker", "logs", "test-farm-007-client-001"]:
            return CompletedProcess(
                args=args, returncode=1, stdout="", stderr="log harvest failed"
            )
        if args == ["docker", "logs", "test-farm-007-client-002"]:
            return CompletedProcess(
                args=args, returncode=0, stdout="client-002 log\n", stderr=""
            )
        if args == ["docker", "rm", "--force", "test-farm-007-client-001"]:
            return CompletedProcess(args=args, returncode=1, stdout="", stderr="remove failed")
        if args == ["docker", "network", "rm", "--force", "test-farm-007"]:
            return CompletedProcess(
                args=args, returncode=1, stdout="", stderr="network remove failed"
            )
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    runner = DockerInvocationRunner(invocation_instance=7, command_runner=_command_runner)
    session = runner.start_session(
        client_ids=("client-001", "client-002"),
        controller_reportback_url="http://192.168.1.10:8080",
        update_server_url="http://192.168.1.10:8081",
        bundle_id="baseline",
    )

    asyncio.run(session.wait_for_subjects())
    first_error = asyncio.run(
        session.finalize(
            invocation_dir=tmp_path / "007",
            failed_client_ids=("client-001", "client-002"),
            keep_containers=False,
        )
    )
    observed_call_count = len(observed_calls)
    second_error = asyncio.run(
        session.finalize(
            invocation_dir=tmp_path / "007",
            failed_client_ids=("client-001", "client-002"),
            keep_containers=False,
        )
    )

    assert first_error is not None
    assert second_error == first_error
    assert len(observed_calls) == observed_call_count
    assert "client-001" in first_error
    assert "client-002" in (tmp_path / "007" / "client-002.log").read_text(encoding="utf-8")
    assert "remove failed" in first_error
    assert "network remove failed" in first_error


@pytest.mark.usefixtures("docker_available_for_runtime_invocation")
def test_docker_invocation_session_removes_router_and_server_artifacts_after_routed_run(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        if args[:3] == ["docker", "image", "inspect"]:
            return CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        return CompletedProcess(args=args, returncode=0, stdout="container-id\n", stderr="")

    bundle_file = tmp_path / "baseline"
    bundle_file.write_bytes(b"bundle bytes from mounted file\n")
    monkeypatch.setattr(
        "test_farm.runtime.invocation.docker.which",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        "test_farm.runtime.invocation.docker.DEFAULT_BUNDLE_FILE",
        bundle_file,
        raising=False,
    )
    monkeypatch.setattr(
        "test_farm.runtime.invocation.docker._wait_for_http_health",
        lambda url: asyncio.sleep(0),
    )

    runner = DockerInvocationRunner(invocation_instance=7, command_runner=_command_runner)
    asyncio.run(runner.start_update_server(bind_address="192.168.1.10:8081"))
    session = runner.start_session(
        client_ids=("client-001",),
        controller_reportback_url="http://192.168.1.10:8080",
        update_server_url="http://10.0.7.2:8081",
        bundle_id="baseline",
    )

    asyncio.run(session.wait_for_subjects())
    finalization_error = asyncio.run(
        session.finalize(
            invocation_dir=tmp_path / "007",
            failed_client_ids=tuple(),
            keep_containers=False,
        )
    )

    assert finalization_error is None
    assert [args for args, _cwd in observed_calls[-8:]] == [
        ["docker", "wait", "test-farm-007-client-001"],
        ["docker", "stop", "test-farm-007-update-server"],
        ["docker", "stop", "test-farm-007-router"],
        ["docker", "rm", "--force", "test-farm-007-client-001"],
        ["docker", "rm", "--force", "test-farm-007-update-server"],
        ["docker", "rm", "--force", "test-farm-007-router"],
        ["docker", "network", "rm", "--force", "test-farm-007"],
        ["docker", "network", "rm", "--force", "test-farm-007-server-network"],
    ]
