"""Prepare runtime assets needed by test-farm invocations."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from subprocess import CompletedProcess

from test_farm.runtime.command_runner import CommandRunner

PREPARED_TOY_CLIENT_IMAGE_TAG = "test-farm/toy-client-runtime:latest"
PREPARED_TOY_UPDATE_SERVER_IMAGE_TAG = "test-farm/toy-update-server-runtime:latest"
PREPARED_ROUTER_IMAGE_TAG = "test-farm/router-runtime:latest"
REPO_ROOT = Path(__file__).resolve().parents[3]
TOY_CLIENT_RUNTIME_ASSETS_DIR = REPO_ROOT / "runtime" / "toy_client"
TOY_CLIENT_RUNTIME_DOCKERFILE = TOY_CLIENT_RUNTIME_ASSETS_DIR / "Dockerfile"
TOY_UPDATE_SERVER_RUNTIME_ASSETS_DIR = REPO_ROOT / "runtime" / "toy_update_server"
TOY_UPDATE_SERVER_RUNTIME_DOCKERFILE = TOY_UPDATE_SERVER_RUNTIME_ASSETS_DIR / "Dockerfile"
ROUTER_RUNTIME_ASSETS_DIR = REPO_ROOT / "runtime" / "router"
ROUTER_RUNTIME_DOCKERFILE = ROUTER_RUNTIME_ASSETS_DIR / "Dockerfile"


class RuntimePreparationError(RuntimeError):
    """Raised when runtime preparation cannot complete."""


@dataclass(frozen=True)
class RuntimePreparationResult:
    """Summarize one runtime preparation attempt."""

    image_tag: str
    created: bool


def prepare_toy_update_server_runtime(
    *,
    command_runner: CommandRunner | None = None,
    force_rebuild: bool = False,
) -> RuntimePreparationResult:
    return _prepare_toy_image_runtime(
        img_name=PREPARED_TOY_UPDATE_SERVER_IMAGE_TAG,
        runtime_dockerfile=TOY_UPDATE_SERVER_RUNTIME_DOCKERFILE,
        runtime_name="toy-update-server",
        command_runner=command_runner,
        force_rebuild=force_rebuild,
    )


def prepare_toy_client_runtime(
    *,
    command_runner: CommandRunner | None = None,
    force_rebuild: bool = False,
) -> RuntimePreparationResult:
    return _prepare_toy_image_runtime(
        img_name=PREPARED_TOY_CLIENT_IMAGE_TAG,
        runtime_dockerfile=TOY_CLIENT_RUNTIME_DOCKERFILE,
        runtime_name="toy-client",
        command_runner=command_runner,
        force_rebuild=force_rebuild,
    )


def prepare_router_runtime(
    *,
    command_runner: CommandRunner | None = None,
    force_rebuild: bool = False,
) -> RuntimePreparationResult:
    return _prepare_toy_image_runtime(
        img_name=PREPARED_ROUTER_IMAGE_TAG,
        runtime_dockerfile=ROUTER_RUNTIME_DOCKERFILE,
        runtime_name="router",
        command_runner=command_runner,
        force_rebuild=force_rebuild,
    )


def _prepare_toy_image_runtime(
    *,
    img_name: str,
    runtime_dockerfile: Path,
    runtime_name: str,
    command_runner: CommandRunner | None = None,
    force_rebuild: bool = False,
) -> RuntimePreparationResult:
    """Ensure the prepared runtime image exists.

    :param command_runner: Optional command runner override for tests.
    :param force_rebuild: When true, rebuild even if the tagged image already exists.
    :returns: Summary of the preparation attempt.
    :raises RuntimePreparationError: Raised when Docker is unavailable or build fails.
    """

    if which("docker") is None:
        raise RuntimePreparationError(
            f"Docker CLI is required to prepare the {runtime_name} runtime."
        )

    runner = _default_command_runner if command_runner is None else command_runner
    if not force_rebuild:
        inspect_result = runner(
            ["docker", "image", "inspect", img_name],
            cwd=REPO_ROOT,
        )
        if inspect_result.returncode == 0:
            return RuntimePreparationResult(
                image_tag=img_name,
                created=False,
            )

    build_result = runner(
        [
            "docker",
            "build",
            "--file",
            str(runtime_dockerfile),
            "--tag",
            img_name,
            ".",
        ],
        cwd=REPO_ROOT,
    )
    if build_result.returncode != 0:
        raise RuntimePreparationError(
            _build_error_detail(stderr=build_result.stderr, runtime_name=runtime_name)
        )

    return RuntimePreparationResult(
        image_tag=img_name,
        created=True,
    )


def _default_command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _build_error_detail(*, stderr: str, runtime_name: str) -> str:
    detail = stderr.strip()
    if detail == "":
        return f"Docker failed to build the {runtime_name} runtime image."
    return f"Docker failed to build the {runtime_name} runtime image: {detail}"
