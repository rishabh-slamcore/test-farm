import socket
from dataclasses import dataclass
from ipaddress import AddressValueError, IPv4Address
from pathlib import Path

from test_farm.runtime.invocation_protocol import RuntimeSetupError


@dataclass(frozen=True)
class ReachableServiceEndpoint:
    """Concrete host endpoint that runtime-isolated clients can reach."""

    host: str
    port: int


def parse_reachable_service_endpoint(bind_address: str) -> ReachableServiceEndpoint:
    """Validate one host bind address for runtime-reachable services.

    :param bind_address: Candidate bind address in ``host:port`` form.
    :returns: Parsed reachable endpoint.
    :raises ValueError: Raised when the bind address is out of contract.
    """

    host, separator, port_text = bind_address.rpartition(":")
    if separator == "" or host == "":
        raise ValueError(f"Controller bind address must be host:port, got {bind_address}.")

    try:
        port = int(port_text)
    except ValueError as error:
        raise ValueError(
            f"Controller bind address must end with an integer port, got {bind_address}."
        ) from error

    if port < 0 or port > 65535:
        raise ValueError(
            f"Controller bind address port must be between 0 and 65535, got {port}."
        )

    try:
        host_address = IPv4Address(host)
    except AddressValueError as error:
        raise ValueError(
            f"Controller bind address must use an IPv4 address, got {host}."
        ) from error

    if host_address.is_loopback or host_address.is_unspecified:
        raise ValueError(
            "Controller bind address must use a concrete non-loopback IPv4 address so "
            "runtime-isolated clients can reach the host-side services."
        )

    return ReachableServiceEndpoint(host=host, port=port)


def service_url(bind_address: str) -> str:
    """Return the HTTP URL for one validated bind address."""

    endpoint = parse_reachable_service_endpoint(bind_address)
    return f"http://{endpoint.host}:{endpoint.port}"


def derive_update_server_bind_address(controller_bind_address: str) -> str:
    """Allocate a reachable Update Server bind address from the Controller host."""

    endpoint = parse_reachable_service_endpoint(controller_bind_address)
    update_server_port = _allocate_port(endpoint.host)
    return f"{endpoint.host}:{update_server_port}"


def _allocate_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind((host, 0))
        resolved_host, port = server_socket.getsockname()
    if resolved_host != host:
        raise RuntimeSetupError(
            f"Expected to allocate Update Server port on {host}, got {resolved_host}."
        )
    return int(port)
