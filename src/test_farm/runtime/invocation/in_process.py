import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Mapping

from test_farm.runtime.invocation_protocol import InvocationSession
from test_farm.subjects.toy_client import (
    BUNDLE_ID_ENV,
    CLIENT_ID_ENV,
    CONTROLLER_REPORTBACK_URL_ENV,
    INVOCATION_INSTANCE_ENV,
    UPDATE_SERVER_URL_ENV,
    run_toy_client,
)
from test_farm.subjects.update_server import UpdateServer


class InProcessInvocationRunner:
    """Run toy clients as host-side tasks behind the runtime boundary."""

    def __init__(self, invocation_instance: int) -> None:
        self._update_server: UpdateServer | None = None
        self._invocation_instance = invocation_instance

    async def start_update_server(self, bind_address: str) -> str:
        if self._update_server is not None:
            raise RuntimeError("Server has already started")
        self._update_server = UpdateServer(bind_address=bind_address)
        await self._update_server.start()
        return self._update_server.base_url

    def start_session(
        self,
        *,
        client_ids: tuple[str, ...],
        controller_reportback_url: str,
        update_server_url: str,
        bundle_id: str,
    ) -> InvocationSession:
        tasks: dict[str, asyncio.Task[int]] = {}
        for client_id in client_ids:
            tasks[client_id] = asyncio.create_task(
                run_toy_client(
                    {
                        INVOCATION_INSTANCE_ENV: str(self._invocation_instance),
                        CLIENT_ID_ENV: client_id,
                        UPDATE_SERVER_URL_ENV: update_server_url,
                        CONTROLLER_REPORTBACK_URL_ENV: controller_reportback_url,
                        BUNDLE_ID_ENV: bundle_id,
                    }
                )
            )
        return InProcessInvocationSession(tasks=tasks)


class InProcessInvocationSession:
    """In-process session used by tests and local semantics checks."""

    def __init__(self, *, tasks: dict[str, asyncio.Task[int]]) -> None:
        self._tasks = tasks

    @property
    def started_client_ids(self) -> tuple[str, ...]:
        return tuple(self._tasks.keys())

    @property
    def startup_failures(self) -> Mapping[str, str]:
        return {}

    async def wait_for_subjects(self) -> None:
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def stop_remaining_subjects(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def finalize(
        self,
        *,
        invocation_dir: Path,
        failed_client_ids: tuple[str, ...],
        keep_containers: bool,
    ) -> str | None:
        del invocation_dir
        del failed_client_ids
        del keep_containers
        return None
