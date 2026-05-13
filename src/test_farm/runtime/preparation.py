"""Prepare runtime assets needed by test-farm invocations."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from subprocess import CompletedProcess

from test_farm.runtime.command_runner import CommandRunner

PREPARED_TOY_CLIENT_IMAGE_TAG = "test-farm/toy-client-runtime:latest"
REPO_ROOT = Path(__file__).resolve().parents[3]
TOY_CLIENT_RUNTIME_ASSETS_DIR = REPO_ROOT / "runtime" / "toy_client"
TOY_CLIENT_RUNTIME_DOCKERFILE = TOY_CLIENT_RUNTIME_ASSETS_DIR / "Dockerfile"


class RuntimePreparationError(RuntimeError):
    """Raised when runtime preparation cannot complete."""


@dataclass(frozen=True)
class RuntimePreparationResult:
    """Summarize one runtime preparation attempt."""

    image_tag: str
    created: bool


def prepare_toy_client_runtime(
    *,
    command_runner: CommandRunner | None = None,
    force_rebuild: bool = False,
) -> RuntimePreparationResult:
    """Ensure the prepared toy-client runtime image exists.

    :param command_runner: Optional command runner override for tests.
    :param force_rebuild: When true, rebuild even if the tagged image already exists.
    :returns: Summary of the preparation attempt.
    :raises RuntimePreparationError: Raised when Docker is unavailable or build fails.
    """

    if which("docker") is None:
        raise RuntimePreparationError(
            "Docker CLI is required to prepare the toy-client runtime."
        )

    runner = _default_command_runner if command_runner is None else command_runner
    if not force_rebuild:
        inspect_result = runner(
            ["docker", "image", "inspect", PREPARED_TOY_CLIENT_IMAGE_TAG],
            cwd=REPO_ROOT,
        )
        if inspect_result.returncode == 0:
            return RuntimePreparationResult(
                image_tag=PREPARED_TOY_CLIENT_IMAGE_TAG,
                created=False,
            )

    build_result = runner(
        [
            "docker",
            "build",
            "--file",
            str(TOY_CLIENT_RUNTIME_DOCKERFILE),
            "--tag",
            PREPARED_TOY_CLIENT_IMAGE_TAG,
            ".",
        ],
        cwd=REPO_ROOT,
    )
    if build_result.returncode != 0:
        raise RuntimePreparationError(_build_error_detail(build_result.stderr))

    return RuntimePreparationResult(
        image_tag=PREPARED_TOY_CLIENT_IMAGE_TAG,
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


def _build_error_detail(stderr: str) -> str:
    detail = stderr.strip()
    if detail == "":
        return "Docker failed to build the toy-client runtime image."
    return f"Docker failed to build the toy-client runtime image: {detail}"
