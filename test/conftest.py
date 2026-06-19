"""Shared pytest fixtures for test suite."""

import socket
from collections.abc import Callable

import pytest
from pytest import Config, Item, MonkeyPatch, fixture

from test_farm.disruptor.models import DiscoveredDevice

DiscoveredDevicesFactory = Callable[[int], list[DiscoveredDevice]]


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register suite options used by local and host-side tests."""

    parser.addoption(
        "--run-host-only",
        action="store_true",
        default=False,
        help="Run tests marked host_only that require real host networking.",
    )


def pytest_collection_modifyitems(config: Config, items: list[Item]) -> None:
    """Skip host-only tests unless explicitly enabled."""

    if config.getoption("--run-host-only"):
        return

    skip_host_only = pytest.mark.skip(reason="host-only test; rerun with --run-host-only")
    for item in items:
        if "host_only" in item.keywords:
            item.add_marker(skip_host_only)


@fixture
def docker_available_for_runtime_preparation(monkeypatch: MonkeyPatch) -> None:
    """Pretend Docker CLI is available for runtime preparation tests."""
    monkeypatch.setattr("test_farm.runtime.preparation.which", lambda name: f"/usr/bin/{name}")


@fixture
def docker_available_for_runtime_invocation(monkeypatch: MonkeyPatch) -> None:
    """Pretend Docker CLI is available for runtime invocation tests."""
    monkeypatch.setattr(
        "test_farm.runtime.invocation.docker.which", lambda name: f"/usr/bin/{name}"
    )


@fixture
def bind_address_factory() -> Callable[[], str]:
    """Allocate concrete loopback bind addresses for host-side test servers."""

    def _allocate_bind_address() -> str:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            local_ip = probe.getsockname()[0]
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
                server_socket.bind((local_ip, 0))
                host, port = server_socket.getsockname()

        return f"{host}:{port}"

    return _allocate_bind_address


@fixture
def reachable_bind_address(bind_address_factory: Callable[[], str]) -> str:
    """Return a deterministic concrete bind address for tests that do not bind."""
    return bind_address_factory()


@fixture
def reachable_update_server_bind_address(bind_address_factory: Callable[[], str]) -> str:
    """Return a deterministic update-server address for tests that do not bind."""
    return bind_address_factory()


@pytest.fixture
def discovered_devices() -> DiscoveredDevicesFactory:
    def build_discovered_devices(device_count: int) -> list[DiscoveredDevice]:
        return [
            DiscoveredDevice(
                device_id=f"sc-aware-{index+10}",
                ip_address=f"192.0.2.{index + 10}",
            )
            for index in range(device_count)
        ]

    return build_discovered_devices
