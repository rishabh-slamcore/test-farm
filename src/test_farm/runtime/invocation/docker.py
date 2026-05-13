import asyncio
from pathlib import Path
from shutil import which
from subprocess import CompletedProcess, run
from typing import Mapping

from test_farm.identifiers import (
    client_diagnostic_log_name,
    runtime_container_name,
    runtime_network_name,
)
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
        network_name = runtime_network_name(invocation_instance)

        network_create_result = self._command_runner(
            ["docker", "network", "create", network_name],
            cwd=self._repo_root,
        )
        if network_create_result.returncode != 0:
            raise RuntimeSetupError(
                _docker_error_detail(
                    stderr=network_create_result.stderr,
                    fallback=f"Docker failed to create runtime network {network_name}.",
                )
            )

        for client_id in client_ids:
            container_name = runtime_container_name(
                invocation_instance=invocation_instance,
                client_id=client_id,
            )
            run_result = self._command_runner(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    container_name,
                    "--network",
                    network_name,
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
            network_name=network_name,
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
        network_name: str,
    ) -> None:
        self._command_runner = command_runner
        self._repo_root = repo_root
        self._started_client_ids = started_client_ids
        self._container_names_by_client_id = container_names_by_client_id
        self._startup_failures = dict(startup_failures)
        self._network_name = network_name
        self._finalization_result: str | None = None
        self._finalized = False

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

    async def finalize(
        self,
        *,
        invocation_dir: Path,
        failed_client_ids: tuple[str, ...],
        keep_containers: bool,
    ) -> str | None:
        if self._finalized:
            return self._finalization_result

        invocation_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []

        for client_id in failed_client_ids:
            container_name = self._container_names_by_client_id.get(client_id)
            if container_name is None:
                continue
            self._harvest_client_logs(
                invocation_dir=invocation_dir,
                client_id=client_id,
                container_name=container_name,
                errors=errors,
            )

        if not keep_containers:
            for container_name in self._container_names_by_client_id.values():
                self._remove_container(container_name=container_name, errors=errors)
            self._remove_network(errors=errors)

        self._finalization_result = "; ".join(errors) if errors else None
        self._finalized = True
        return self._finalization_result

    def _harvest_client_logs(
        self,
        *,
        invocation_dir: Path,
        client_id: str,
        container_name: str,
        errors: list[str],
    ) -> None:
        log_result = self._command_runner(
            ["docker", "logs", container_name],
            cwd=self._repo_root,
        )
        if log_result.returncode != 0:
            errors.append(
                f"Failed to harvest logs for {client_id}: "
                f"{_docker_error_detail(stderr=log_result.stderr, fallback='docker logs failed.')}"
            )
            return

        (invocation_dir / client_diagnostic_log_name(client_id)).write_text(
            f"{log_result.stdout}{log_result.stderr}",
            encoding="utf-8",
        )

    def _remove_container(self, *, container_name: str, errors: list[str]) -> None:
        remove_result = self._command_runner(
            ["docker", "rm", "--force", container_name],
            cwd=self._repo_root,
        )
        if remove_result.returncode != 0:
            errors.append(
                f"Failed to remove container {container_name}: "
                f"{_docker_error_detail(stderr=remove_result.stderr, fallback='docker rm failed.')}"
            )

    def _remove_network(self, *, errors: list[str]) -> None:
        network_remove_result = self._command_runner(
            ["docker", "network", "rm", self._network_name],
            cwd=self._repo_root,
        )
        if network_remove_result.returncode != 0:
            errors.append(
                f"Failed to remove runtime network {self._network_name}: "
                f"{_docker_error_detail(stderr=network_remove_result.stderr, fallback='docker network rm failed.')}"
            )


def _docker_error_detail(*, stderr: str, fallback: str) -> str:
    detail = stderr.strip()
    if detail == "":
        return fallback
    return detail


def _default_command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
    return run(args, cwd=cwd, check=False, capture_output=True, text=True)
