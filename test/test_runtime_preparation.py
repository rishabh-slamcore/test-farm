"""Runtime preparation behavior tests."""

from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest import MonkeyPatch

from test_farm.runtime.preparation import (
    PREPARED_TOY_CLIENT_IMAGE_TAG,
    REPO_ROOT,
    RuntimePreparationError,
    prepare_toy_client_runtime,
)


def test_prepare_toy_client_runtime_skips_build_when_image_already_exists(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("test_farm.runtime.preparation.which", lambda name: f"/usr/bin/{name}")

    result = prepare_toy_client_runtime(command_runner=_command_runner)

    assert result.image_tag == PREPARED_TOY_CLIENT_IMAGE_TAG
    assert result.created is False
    assert observed_calls == [
        (
            ["docker", "image", "inspect", PREPARED_TOY_CLIENT_IMAGE_TAG],
            Path("/home/rishi/work/test-farm"),
        )
    ]


def test_prepare_toy_client_runtime_builds_image_when_missing(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        if args[:3] == ["docker", "image", "inspect"]:
            return CompletedProcess(args=args, returncode=1, stdout="", stderr="missing")
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("test_farm.runtime.preparation.which", lambda name: f"/usr/bin/{name}")

    result = prepare_toy_client_runtime(command_runner=_command_runner)

    assert result.image_tag == PREPARED_TOY_CLIENT_IMAGE_TAG
    assert result.created is True
    assert observed_calls == [
        (
            ["docker", "image", "inspect", PREPARED_TOY_CLIENT_IMAGE_TAG],
            REPO_ROOT,
        ),
        (
            [
                "docker",
                "build",
                "--file",
                str(REPO_ROOT / "runtime" / "toy_client" / "Dockerfile"),
                "--tag",
                PREPARED_TOY_CLIENT_IMAGE_TAG,
                ".",
            ],
            REPO_ROOT,
        ),
    ]


def test_prepare_toy_client_runtime_rebuilds_when_forced(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_calls: list[tuple[list[str], Path]] = []

    def _command_runner(args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        observed_calls.append((args, cwd))
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("test_farm.runtime.preparation.which", lambda name: f"/usr/bin/{name}")

    result = prepare_toy_client_runtime(
        command_runner=_command_runner,
        force_rebuild=True,
    )

    assert result.image_tag == PREPARED_TOY_CLIENT_IMAGE_TAG
    assert result.created is True
    assert observed_calls == [
        (
            [
                "docker",
                "build",
                "--file",
                str(REPO_ROOT / "runtime" / "toy_client" / "Dockerfile"),
                "--tag",
                PREPARED_TOY_CLIENT_IMAGE_TAG,
                ".",
            ],
            REPO_ROOT,
        ),
    ]


def test_prepare_toy_client_runtime_fails_clearly_when_docker_is_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("test_farm.runtime.preparation.which", lambda name: None)

    with pytest.raises(RuntimePreparationError) as error:
        prepare_toy_client_runtime()

    assert str(error.value) == "Docker CLI is required to prepare the toy-client runtime."
