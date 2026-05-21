"""Runtime preparation behavior tests."""

from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest import MonkeyPatch

from test_farm.runtime.preparation import (
    PREPARED_ROUTER_IMAGE_TAG,
    PREPARED_TOY_CLIENT_IMAGE_TAG,
    PREPARED_TOY_UPDATE_SERVER_IMAGE_TAG,
    REPO_ROOT,
    RuntimePreparationError,
    RuntimePreparationResult,
    prepare_router_runtime,
    prepare_toy_client_runtime,
    prepare_toy_update_server_runtime,
)

PrepareRuntime = Callable[..., RuntimePreparationResult]

RUNTIME_CASES = (
    pytest.param(
        prepare_toy_client_runtime,
        PREPARED_TOY_CLIENT_IMAGE_TAG,
        REPO_ROOT / "runtime" / "toy_client" / "Dockerfile",
        "Docker CLI is required to prepare the toy-client runtime.",
        id="toy-client",
    ),
    pytest.param(
        prepare_toy_update_server_runtime,
        PREPARED_TOY_UPDATE_SERVER_IMAGE_TAG,
        REPO_ROOT / "runtime" / "toy_update_server" / "Dockerfile",
        "Docker CLI is required to prepare the toy-update-server runtime.",
        id="toy-update-server",
    ),
    pytest.param(
        prepare_router_runtime,
        PREPARED_ROUTER_IMAGE_TAG,
        REPO_ROOT / "runtime" / "router" / "Dockerfile",
        "Docker CLI is required to prepare the router runtime.",
        id="router",
    ),
)


@pytest.mark.parametrize(
    ("prepare_runtime", "image_tag", "runtime_dockerfile", "docker_unavailable_message"),
    RUNTIME_CASES,
)
@pytest.mark.usefixtures("docker_available_for_runtime_preparation")
def test_prepare_runtime_skips_build_when_image_already_exists(
    prepare_runtime: PrepareRuntime,
    image_tag: str,
    runtime_dockerfile: Path,
    docker_unavailable_message: str,
) -> None:
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    result = prepare_runtime(command_runner=_command_runner)

    assert result.image_tag == image_tag
    assert result.created is False
    assert observed_calls == [
        (
            ["docker", "image", "inspect", image_tag],
            REPO_ROOT,
        )
    ]


@pytest.mark.parametrize(
    ("prepare_runtime", "image_tag", "runtime_dockerfile", "docker_unavailable_message"),
    RUNTIME_CASES,
)
# @pytest.mark.usefixtures("docker_available_for_runtime_preparation")
def test_prepare_runtime_builds_image_when_missing(
    prepare_runtime: PrepareRuntime,
    image_tag: str,
    runtime_dockerfile: Path,
    docker_unavailable_message: str,
) -> None:
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        if args[:3] == ["docker", "image", "inspect"]:
            return CompletedProcess(args=args, returncode=1, stdout="", stderr="missing")
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    result = prepare_runtime(command_runner=_command_runner)

    assert result.image_tag == image_tag
    assert result.created is True
    assert observed_calls == [
        (
            ["docker", "image", "inspect", image_tag],
            REPO_ROOT,
        ),
        (
            [
                "docker",
                "build",
                "--file",
                str(runtime_dockerfile),
                "--tag",
                image_tag,
                ".",
            ],
            REPO_ROOT,
        ),
    ]


@pytest.mark.parametrize(
    ("prepare_runtime", "image_tag", "runtime_dockerfile", "docker_unavailable_message"),
    RUNTIME_CASES,
)
@pytest.mark.usefixtures("docker_available_for_runtime_preparation")
def test_prepare_runtime_rebuilds_when_forced(
    prepare_runtime: PrepareRuntime,
    image_tag: str,
    runtime_dockerfile: Path,
    docker_unavailable_message: str,
) -> None:
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    result = prepare_runtime(
        command_runner=_command_runner,
        force_rebuild=True,
    )

    assert result.image_tag == image_tag
    assert result.created is True
    assert observed_calls == [
        (
            [
                "docker",
                "build",
                "--file",
                str(runtime_dockerfile),
                "--tag",
                image_tag,
                ".",
            ],
            REPO_ROOT,
        ),
    ]


@pytest.mark.parametrize(
    ("prepare_runtime", "image_tag", "runtime_dockerfile", "docker_unavailable_message"),
    RUNTIME_CASES,
)
def test_prepare_runtime_fails_clearly_when_docker_is_unavailable(
    prepare_runtime: PrepareRuntime,
    image_tag: str,
    runtime_dockerfile: Path,
    docker_unavailable_message: str,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("test_farm.runtime.preparation.which", lambda name: None)

    with pytest.raises(RuntimePreparationError) as error:
        prepare_runtime()

    assert str(error.value) == docker_unavailable_message
