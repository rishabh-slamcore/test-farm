"""Runtime-facing invocation helpers and abstractions."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol


class RuntimeSetupError(RuntimeError):
    """Raised when runtime setup fails before any client launch."""


class InvocationSession(Protocol):
    """Abstract runtime session for one invocation."""

    @property
    def started_client_ids(self) -> tuple[str, ...]:
        """Return every Client ID whose runtime workload started."""
        raise NotImplementedError

    @property
    def startup_failures(self) -> Mapping[str, str]:
        """Return per-client startup failures keyed by Client ID."""
        raise NotImplementedError

    async def wait_for_subjects(self) -> None:
        """Wait for every started subject to exit."""
        raise NotImplementedError

    async def stop_remaining_subjects(self) -> None:
        """Stop any subjects still running."""
        raise NotImplementedError

    async def finalize(
        self,
        *,
        invocation_dir: Path,
        failed_client_ids: tuple[str, ...],
        keep_containers: bool,
    ) -> str | None:
        """Harvest diagnostics and apply teardown or preservation policy once."""
        raise NotImplementedError


class InvocationRunner(Protocol):
    """Start one runtime session for an invocation."""

    def start_session(
        self,
        *,
        invocation_instance: int,
        client_ids: tuple[str, ...],
        controller_reportback_url: str,
        update_server_url: str,
        bundle_id: str,
    ) -> InvocationSession:
        """Start the invocation runtime session."""
        raise NotImplementedError
