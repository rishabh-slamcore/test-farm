import asyncio
from pathlib import Path
from shutil import which
from subprocess import CompletedProcess, run
from typing import Mapping

from test_farm.identifiers import invocation_directory_name
from test_farm.runtime.command_runner import CommandRunner
from test_farm.runtime.invocation_protocol import InvocationSession, RuntimeSetupError
from test_farm.runtime.preparation import PREPARED_TOY_CLIENT_IMAGE_TAG, REPO_ROOT
from test_farm.subjects.toy_client import (
    BUNDLE_ID_ENV,
    CLIENT_ID_ENV,
    CONTROLLER_REPORTBACK_URL_ENV,
    INVOCATION_INSTANCE_ENV,
    UPDATE_SERVER_URL_ENV,
)


class DockerInvocationRunner:
    """Launch toy clients as runtime-isolated Docker workloads."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        repo_root: Path = REPO_ROOT,
    ) -> None:
        self._command_runner = (
            _default_command_runner if command_runner is None else command_runner
        )
        self._repo_root = repo_root

    def start_session(
        self,
        *,
        invocation_instance: int,
        client_ids: tuple[str, ...],
        controller_reportback_url: str,
        update_server_url: str,
        bundle_id: str,
    ) -> InvocationSession:
        if which("docker") is None:
            raise RuntimeSetupError("Docker CLI is required to run the baseline invocation.")

        inspect_result = self._command_runner(
            ["docker", "image", "inspect", PREPARED_TOY_CLIENT_IMAGE_TAG],
            cwd=self._repo_root,
        )
        if inspect_result.returncode != 0:
            raise RuntimeSetupError(
                "Baseline toy-client runtime image "
                f"{PREPARED_TOY_CLIENT_IMAGE_TAG} is missing. "
                "Run `test-farm prepare-runtime` first."
            )

        started_client_ids: list[str] = []
        container_names_by_client_id: dict[str, str] = {}
        startup_failures: dict[str, str] = {}

        for client_id in client_ids:
            container_name = _container_name(
                invocation_instance=invocation_instance,
                client_id=client_id,
            )
            run_result = self._command_runner(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    container_name,
                    "--env",
                    f"{INVOCATION_INSTANCE_ENV}={invocation_instance}",
                    "--env",
                    f"{CLIENT_ID_ENV}={client_id}",
                    "--env",
                    f"{UPDATE_SERVER_URL_ENV}={update_server_url}",
                    "--env",
                    f"{CONTROLLER_REPORTBACK_URL_ENV}={controller_reportback_url}",
                    "--env",
                    f"{BUNDLE_ID_ENV}={bundle_id}",
                    PREPARED_TOY_CLIENT_IMAGE_TAG,
                ],
                cwd=self._repo_root,
            )
            if run_result.returncode != 0:
                startup_failures[client_id] = _docker_error_detail(
                    stderr=run_result.stderr,
                    fallback=f"Docker failed to start runtime-isolated client {client_id}.",
                )
                continue

            started_client_ids.append(client_id)
            container_names_by_client_id[client_id] = container_name

        return DockerInvocationSession(
            command_runner=self._command_runner,
            repo_root=self._repo_root,
            started_client_ids=tuple(started_client_ids),
            container_names_by_client_id=container_names_by_client_id,
            startup_failures=startup_failures,
        )


class DockerInvocationSession:
    """Docker-backed runtime session."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner,
        repo_root: Path,
        started_client_ids: tuple[str, ...],
        container_names_by_client_id: dict[str, str],
        startup_failures: dict[str, str],
    ) -> None:
        self._command_runner = command_runner
        self._repo_root = repo_root
        self._started_client_ids = started_client_ids
        self._container_names_by_client_id = container_names_by_client_id
        self._startup_failures = dict(startup_failures)

    @property
    def started_client_ids(self) -> tuple[str, ...]:
        return self._started_client_ids

    @property
    def startup_failures(self) -> Mapping[str, str]:
        return dict(self._startup_failures)

    async def wait_for_subjects(self) -> None:
        await asyncio.gather(
            *(
                asyncio.to_thread(self._wait_for_container, container_name)
                for container_name in self._container_names_by_client_id.values()
            )
        )

    async def stop_remaining_subjects(self) -> None:
        await asyncio.gather(
            *(
                asyncio.to_thread(self._stop_container, container_name)
                for container_name in self._container_names_by_client_id.values()
            )
        )

    def _wait_for_container(self, container_name: str) -> None:
        self._command_runner(["docker", "wait", container_name], cwd=self._repo_root)

    def _stop_container(self, container_name: str) -> None:
        self._command_runner(["docker", "stop", container_name], cwd=self._repo_root)


def _container_name(*, invocation_instance: int, client_id: str) -> str:
    return f"test-farm-{invocation_directory_name(invocation_instance)}-{client_id}"


def _docker_error_detail(*, stderr: str, fallback: str) -> str:
    detail = stderr.strip()
    if detail == "":
        return fallback
    return detail


def _default_command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
    return run(args, cwd=cwd, check=False, capture_output=True, text=True)
