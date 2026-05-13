"""Runtime invocation boundary tests."""

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from test_farm.runtime.invocation.docker import DockerInvocationRunner


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

    runner = DockerInvocationRunner(command_runner=_command_runner)

    session = runner.start_session(
        invocation_instance=7,
        client_ids=("client-001", "client-002"),
        controller_reportback_url="http://192.168.1.10:8080",
        update_server_url="http://192.168.1.10:8081",
        bundle_id="baseline",
    )

    assert session.started_client_ids == ("client-002",)
    assert dict(session.startup_failures) == {"client-001": "client-001 failed to start"}
    assert [args for args, _cwd in observed_calls] == [
        ["docker", "image", "inspect", "test-farm/toy-client-runtime:latest"],
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            "test-farm-007-client-001",
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
            "--rm",
            "--name",
            "test-farm-007-client-002",
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
    ]
