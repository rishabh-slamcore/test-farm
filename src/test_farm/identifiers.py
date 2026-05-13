"""Deterministic identifier helpers shared across baseline invocation code."""

INVOCATION_DIRECTORY_WIDTH = 3


def client_id(client_index: int) -> str:
    """Create the stable client identifier for one client index."""

    return f"client-{client_index:03d}"


def expected_client_ids(client_count: int) -> tuple[str, ...]:
    """Return the stable ordered client identifiers for one invocation."""

    return tuple(client_id(index) for index in range(1, client_count + 1))


def invocation_directory_name(invocation_instance: int) -> str:
    """Return the stable artifact directory name for one invocation."""

    return f"{invocation_instance:0{INVOCATION_DIRECTORY_WIDTH}d}"


def runtime_network_name(invocation_instance: int) -> str:
    """Return the deterministic runtime network name for one invocation."""

    return f"test-farm-{invocation_directory_name(invocation_instance)}"


def runtime_container_name(*, invocation_instance: int, client_id: str) -> str:
    """Return the deterministic runtime container name for one client."""

    return f"{runtime_network_name(invocation_instance)}-{client_id}"


def client_diagnostic_log_name(client_id: str) -> str:
    """Return the deterministic diagnostic log filename for one client."""

    return f"{client_id}.log"
