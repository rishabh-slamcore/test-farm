import asyncio
from dataclasses import dataclass
from ipaddress import IPv4Network
from pathlib import Path
from shutil import which
from subprocess import CompletedProcess, run
from typing import Mapping

import httpx

from test_farm.bundles import DEFAULT_BUNDLE_FILE
from test_farm.identifiers import (
    client_diagnostic_log_name,
    client_runtime_network_name,
    router_container_name,
    runtime_container_name,
    server_container_name,
    server_runtime_network_name,
)
from test_farm.runtime.command_runner import CommandRunner
from test_farm.runtime.invocation_protocol import InvocationSession, RuntimeSetupError
from test_farm.runtime.networking import parse_reachable_service_endpoint, service_url
from test_farm.runtime.preparation import (
    PREPARED_ROUTER_IMAGE_TAG,
    PREPARED_TOY_CLIENT_IMAGE_TAG,
    PREPARED_TOY_UPDATE_SERVER_IMAGE_TAG,
    REPO_ROOT,
)
from test_farm.subjects.toy_client import (
    BUNDLE_ID_ENV,
    CLIENT_ID_ENV,
    CONTROLLER_REPORTBACK_URL_ENV,
    INVOCATION_INSTANCE_ENV,
    UPDATE_SERVER_URL_ENV,
)
from test_farm.subjects.update_server import (
    UPDATE_SERVER_BIND_ADDRESS_ENV,
    UPDATE_SERVER_BUNDLE_DIR_ENV,
)

UPDATE_SERVER_CONTAINER_BUNDLE_DIR = "/test-farm/bundles"
CLIENT_START_GATE_FILE = "/tmp/test-farm-start"
SERVER_HEALTH_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class _RoutedInvocationTopology:
    server_subnet: str
    client_subnet: str
    update_server_ip: str
    router_server_ip: str
    router_client_ip: str

    def client_ip(self, client_index: int) -> str:
        client_network = IPv4Network(self.client_subnet)
        host_index = client_index + 9
        if host_index >= client_network.num_addresses - 1:
            raise RuntimeSetupError(
                f"Invocation supports at most {client_network.num_addresses - 11} clients "
                f"on routed Docker topology, got client index {client_index}."
            )
        return str(client_network[host_index])


class DockerInvocationRunner:
    """Launch toy clients as runtime-isolated Docker workloads."""

    def __init__(
        self,
        *,
        invocation_instance: int,
        command_runner: CommandRunner | None = None,
        repo_root: Path = REPO_ROOT,
    ) -> None:
        self._command_runner = (
            _default_command_runner if command_runner is None else command_runner
        )
        if which("docker") is None:
            raise RuntimeSetupError("Docker CLI is required to run the baseline invocation.")
        self._invocation_instance = invocation_instance
        self._repo_root = repo_root
        self._server_container_name: str | None = None
        self._server_network_name: str | None = None
        self._router_container_name: str | None = None
        self._topology: _RoutedInvocationTopology | None = None

    def _check_image_exists(self, img_name: str) -> None:
        inspect_result = self._command_runner(
            ["docker", "image", "inspect", img_name],
            cwd=self._repo_root,
        )
        if inspect_result.returncode != 0:
            raise RuntimeSetupError(
                f"Prepared runtime image {img_name} is missing. "
                "Run `test-farm prepare-runtime` first."
            )

    def _create_network(
        self,
        *,
        network_name: str,
        subnet: str | None = None,
        stale_container_names: tuple[str, ...] = tuple(),
    ) -> None:
        args = ["docker", "network", "create"]
        if subnet is not None:
            args.extend(["--subnet", subnet])
        args.append(network_name)

        network_create_result = self._command_runner(args, cwd=self._repo_root)
        if network_create_result.returncode == 0:
            return

        if _docker_already_exists(network_create_result.stderr):
            self._cleanup_stale_network(
                network_name=network_name,
                stale_container_names=stale_container_names,
            )
            retry_result = self._command_runner(args, cwd=self._repo_root)
            if retry_result.returncode == 0:
                return
            network_create_result = retry_result

        raise RuntimeSetupError(
            _docker_error_detail(
                stderr=network_create_result.stderr,
                fallback=f"Docker failed to create runtime network {network_name}.",
            )
        )

    def _cleanup_stale_network(
        self,
        *,
        network_name: str,
        stale_container_names: tuple[str, ...],
    ) -> None:
        for container_name in stale_container_names:
            self._force_remove_container(container_name)
        self._command_runner(["docker", "network", "rm", network_name], cwd=self._repo_root)

    def _run_detached_container(
        self,
        *,
        args: list[str],
        container_name: str,
        stale_network_names: tuple[str, ...] = tuple(),
    ) -> CompletedProcess[str]:
        run_result = self._command_runner(args, cwd=self._repo_root)
        if run_result.returncode == 0:
            return run_result

        if _docker_name_conflict(stderr=run_result.stderr, container_name=container_name):
            self._force_remove_container(container_name)
            for network_name in stale_network_names:
                self._command_runner(
                    ["docker", "network", "rm", network_name], cwd=self._repo_root
                )
            retry_result = self._command_runner(args, cwd=self._repo_root)
            if retry_result.returncode == 0:
                return retry_result
            run_result = retry_result

        return run_result

    def _force_remove_container(self, container_name: str) -> None:
        self._command_runner(
            ["docker", "rm", "--force", container_name],
            cwd=self._repo_root,
        )

    async def start_update_server(self, *, bind_address: str) -> str:
        self._check_image_exists(PREPARED_TOY_UPDATE_SERVER_IMAGE_TAG)
        self._check_image_exists(PREPARED_ROUTER_IMAGE_TAG)

        controller_endpoint = parse_reachable_service_endpoint(bind_address)
        self._topology = _build_routed_topology(self._invocation_instance)
        self._server_container_name = server_container_name(self._invocation_instance)
        self._server_network_name = server_runtime_network_name(self._invocation_instance)
        self._router_container_name = router_container_name(self._invocation_instance)

        self._create_network(
            network_name=self._server_network_name,
            subnet=self._topology.server_subnet,
            stale_container_names=(
                self._server_container_name,
                self._router_container_name,
            ),
        )
        self._start_router_container()

        update_server_port = controller_endpoint.port
        update_server_bind_address = f"0.0.0.0:{update_server_port}"
        start_server_result = self._run_detached_container(
            args=[
                "docker",
                "run",
                "--detach",
                "--name",
                self._server_container_name,
                "--network",
                self._server_network_name,
                "--ip",
                self._topology.update_server_ip,
                "--cap-add",
                "NET_ADMIN",
                "--env",
                f"{UPDATE_SERVER_BIND_ADDRESS_ENV}={update_server_bind_address}",
                "--env",
                f"{UPDATE_SERVER_BUNDLE_DIR_ENV}={UPDATE_SERVER_CONTAINER_BUNDLE_DIR}",
                "--mount",
                (
                    "type=bind,"
                    f"source={DEFAULT_BUNDLE_FILE},"
                    f"target={UPDATE_SERVER_CONTAINER_BUNDLE_DIR}/{DEFAULT_BUNDLE_FILE.name},"
                    "readonly"
                ),
                PREPARED_TOY_UPDATE_SERVER_IMAGE_TAG,
            ],
            container_name=self._server_container_name,
            stale_network_names=(self._server_network_name,),
        )
        if start_server_result.returncode != 0:
            raise RuntimeSetupError(
                _docker_error_detail(
                    stderr=start_server_result.stderr,
                    fallback=(
                        "Docker failed to start runtime-isolated update server "
                        f"{self._server_container_name}."
                    ),
                )
            )

        update_server_url = f"http://{self._topology.update_server_ip}:{update_server_port}"
        await _wait_for_http_health(f"{update_server_url}/health")
        return update_server_url

    def _start_router_container(self) -> None:
        if self._router_container_name is None or self._server_network_name is None:
            raise RuntimeError("Router topology has not been initialized.")
        if self._topology is None:
            raise RuntimeError("Router topology has not been initialized.")

        start_router_result = self._run_detached_container(
            args=[
                "docker",
                "run",
                "--detach",
                "--name",
                self._router_container_name,
                "--network",
                self._server_network_name,
                "--ip",
                self._topology.router_server_ip,
                "--cap-add",
                "NET_ADMIN",
                "--sysctl",
                "net.ipv4.ip_forward=1",
                PREPARED_ROUTER_IMAGE_TAG,
            ],
            container_name=self._router_container_name,
            stale_network_names=(self._server_network_name,),
        )
        if start_router_result.returncode != 0:
            raise RuntimeSetupError(
                _docker_error_detail(
                    stderr=start_router_result.stderr,
                    fallback=(
                        "Docker failed to start runtime router "
                        f"{self._router_container_name}."
                    ),
                )
            )

    def start_session(
        self,
        *,
        client_ids: tuple[str, ...],
        controller_reportback_url: str,
        update_server_url: str,
        bundle_id: str,
    ) -> InvocationSession:
        self._check_image_exists(PREPARED_TOY_CLIENT_IMAGE_TAG)

        started_client_ids: list[str] = []
        container_names_by_client_id: dict[str, str] = {}
        startup_failures: dict[str, str] = {}
        network_name = client_runtime_network_name(self._invocation_instance)
        stale_container_names = tuple(
            runtime_container_name(
                invocation_instance=self._invocation_instance,
                client_id=client_id,
            )
            for client_id in client_ids
        )

        self._create_network(
            network_name=network_name,
            subnet=self._topology.client_subnet if self._topology is not None else None,
            stale_container_names=stale_container_names,
        )
        if self._topology is not None and self._router_container_name is not None:
            self._connect_router_to_client_network(network_name=network_name)
            self._configure_update_server_route()

        for client_index, client_id in enumerate(client_ids, start=1):
            container_name = runtime_container_name(
                invocation_instance=self._invocation_instance,
                client_id=client_id,
            )
            run_args = [
                "docker",
                "run",
                "--detach",
                "--name",
                container_name,
                "--network",
                network_name,
            ]
            if self._topology is not None:
                run_args.extend(
                    [
                        "--ip",
                        self._topology.client_ip(client_index),
                        "--cap-add",
                        "NET_ADMIN",
                    ]
                )
            run_args.extend(
                [
                    "--env",
                    f"{INVOCATION_INSTANCE_ENV}={self._invocation_instance}",
                    "--env",
                    f"{CLIENT_ID_ENV}={client_id}",
                    "--env",
                    f"{UPDATE_SERVER_URL_ENV}={update_server_url}",
                    "--env",
                    f"{CONTROLLER_REPORTBACK_URL_ENV}={controller_reportback_url}",
                    "--env",
                    f"{BUNDLE_ID_ENV}={bundle_id}",
                ]
            )
            if self._topology is not None:
                run_args.extend(
                    [
                        "--entrypoint",
                        "sh",
                        PREPARED_TOY_CLIENT_IMAGE_TAG,
                        "-c",
                        (
                            f"while [ ! -f {CLIENT_START_GATE_FILE} ]; do sleep 0.05; done; "
                            "exec python -m test_farm.subjects.toy_client_runtime"
                        ),
                    ]
                )
            else:
                run_args.append(PREPARED_TOY_CLIENT_IMAGE_TAG)

            run_result = self._run_detached_container(
                args=run_args,
                container_name=container_name,
                stale_network_names=(network_name,),
            )
            if run_result.returncode != 0:
                startup_failures[client_id] = _docker_error_detail(
                    stderr=run_result.stderr,
                    fallback=f"Docker failed to start runtime-isolated client {client_id}.",
                )
                continue

            try:
                if self._topology is not None:
                    self._configure_client_route(container_name=container_name)
                    self._release_client_start_gate(container_name=container_name)
            except RuntimeSetupError as error:
                startup_failures[client_id] = str(error)
                continue

            started_client_ids.append(client_id)
            container_names_by_client_id[client_id] = container_name

        return DockerInvocationSession(
            command_runner=self._command_runner,
            repo_root=self._repo_root,
            server_container_name=self._server_container_name,
            router_container_name=self._router_container_name,
            server_network_name=self._server_network_name,
            started_client_ids=tuple(started_client_ids),
            container_names_by_client_id=container_names_by_client_id,
            startup_failures=startup_failures,
            client_network_name=network_name,
        )

    def _connect_router_to_client_network(self, *, network_name: str) -> None:
        if self._router_container_name is None or self._topology is None:
            raise RuntimeError("Router topology has not been initialized.")

        connect_result = self._command_runner(
            [
                "docker",
                "network",
                "connect",
                "--ip",
                self._topology.router_client_ip,
                network_name,
                self._router_container_name,
            ],
            cwd=self._repo_root,
        )
        if connect_result.returncode != 0:
            raise RuntimeSetupError(
                _docker_error_detail(
                    stderr=connect_result.stderr,
                    fallback=(
                        f"Docker failed to connect router {self._router_container_name} "
                        f"to runtime network {network_name}."
                    ),
                )
            )

    def _configure_update_server_route(self) -> None:
        if self._server_container_name is None or self._topology is None:
            raise RuntimeError("Update server topology has not been initialized.")

        self._replace_container_route(
            container_name=self._server_container_name,
            destination=self._topology.client_subnet,
            gateway=self._topology.router_server_ip,
            failure_message=(
                f"Failed to configure explicit route from update server "
                f"{self._server_container_name} to client network {self._topology.client_subnet}."
            ),
        )

    def _configure_client_route(self, *, container_name: str) -> None:
        if self._topology is None:
            raise RuntimeError("Client routing topology has not been initialized.")

        self._replace_container_route(
            container_name=container_name,
            destination=self._topology.server_subnet,
            gateway=self._topology.router_client_ip,
            failure_message=(
                f"Failed to configure explicit route from client container {container_name} "
                f"to update-server network {self._topology.server_subnet}."
            ),
        )

    def _replace_container_route(
        self,
        *,
        container_name: str,
        destination: str,
        gateway: str,
        failure_message: str,
    ) -> None:
        route_result = self._command_runner(
            [
                "docker",
                "exec",
                container_name,
                "ip",
                "route",
                "replace",
                destination,
                "via",
                gateway,
            ],
            cwd=self._repo_root,
        )
        if route_result.returncode != 0:
            raise RuntimeSetupError(
                _docker_error_detail(stderr=route_result.stderr, fallback=failure_message)
            )

    def _release_client_start_gate(self, *, container_name: str) -> None:
        release_result = self._command_runner(
            ["docker", "exec", container_name, "sh", "-c", f"touch {CLIENT_START_GATE_FILE}"],
            cwd=self._repo_root,
        )
        if release_result.returncode != 0:
            raise RuntimeSetupError(
                _docker_error_detail(
                    stderr=release_result.stderr,
                    fallback=f"Failed to release client start gate for {container_name}.",
                )
            )


class DockerInvocationSession:
    """Docker-backed runtime session."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner,
        repo_root: Path,
        server_container_name: str | None,
        router_container_name: str | None,
        server_network_name: str | None,
        started_client_ids: tuple[str, ...],
        container_names_by_client_id: dict[str, str],
        startup_failures: dict[str, str],
        client_network_name: str,
    ) -> None:
        self._command_runner = command_runner
        self._repo_root = repo_root
        self._server_container_name = server_container_name
        self._router_container_name = router_container_name
        self._server_network_name = server_network_name
        self._started_client_ids = started_client_ids
        self._container_names_by_client_id = container_names_by_client_id
        self._startup_failures = dict(startup_failures)
        self._client_network_name = client_network_name
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

        for auxiliary_container_name in (
            self._server_container_name,
            self._router_container_name,
        ):
            if auxiliary_container_name is not None:
                self._stop_container(auxiliary_container_name)

        if not keep_containers:
            for container_name in self._container_names_by_client_id.values():
                self._remove_container(container_name=container_name, errors=errors)
            for auxiliary_container_name in (
                self._server_container_name,
                self._router_container_name,
            ):
                if auxiliary_container_name is not None:
                    self._remove_container(
                        container_name=auxiliary_container_name,
                        errors=errors,
                    )
            self._remove_network(network_name=self._client_network_name, errors=errors)
            if self._server_network_name is not None:
                self._remove_network(network_name=self._server_network_name, errors=errors)

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

    def _remove_network(self, *, network_name: str, errors: list[str]) -> None:
        network_remove_result = self._command_runner(
            ["docker", "network", "rm", "--force", network_name],
            cwd=self._repo_root,
        )
        if network_remove_result.returncode != 0:
            errors.append(
                f"Failed to remove runtime network {network_name}: "
                f"{_docker_error_detail(stderr=network_remove_result.stderr, fallback='docker network rm failed.')}"
            )


def _build_routed_topology(invocation_instance: int) -> _RoutedInvocationTopology:
    second_octet = (invocation_instance // 256) % 256
    third_octet = invocation_instance % 256
    client_third_octet = (third_octet + 128) % 256

    server_network = IPv4Network(f"10.{second_octet}.{third_octet}.0/24")
    client_network = IPv4Network(f"10.{second_octet}.{client_third_octet}.0/24")
    return _RoutedInvocationTopology(
        server_subnet=str(server_network),
        client_subnet=str(client_network),
        update_server_ip=str(server_network[2]),
        router_server_ip=str(server_network[3]),
        router_client_ip=str(client_network[3]),
    )


def _docker_error_detail(*, stderr: str, fallback: str) -> str:
    detail = stderr.strip()
    if detail == "":
        return fallback
    return detail


def _docker_already_exists(stderr: str) -> bool:
    return "already exists" in stderr.lower()


def _docker_name_conflict(*, stderr: str, container_name: str) -> bool:
    detail = stderr.lower()
    return container_name.lower() in detail and "already in use" in detail


async def _wait_for_http_health(url: str) -> None:
    deadline = asyncio.get_running_loop().time() + SERVER_HEALTH_TIMEOUT_SECONDS
    async with httpx.AsyncClient() as client:
        while True:
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass

            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeSetupError(
                    f"Timed out waiting for runtime-isolated update server health at {url}."
                )
            await asyncio.sleep(0.05)


def _default_command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
    return run(args, cwd=cwd, check=False, capture_output=True, text=True)
