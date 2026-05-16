"""Runtime entrypoint for the containerized update server."""

import asyncio
import signal
from os import environ
from typing import Mapping

from test_farm.subjects.update_server import UPDATE_SERVER_BIND_ADDRESS_ENV, UpdateServer


async def run_update_server(environment: Mapping[str, str] | None = None) -> int:
    """Run the Update Server using process or injected environment variables."""

    shutdown_task: asyncio.Task[None] | None = None
    loop = asyncio.get_running_loop()

    try:
        resolved_environment = environ if environment is None else environment
        update_server_bind_address = resolved_environment[UPDATE_SERVER_BIND_ADDRESS_ENV]
        update_server = UpdateServer(bind_address=update_server_bind_address)

        def request_shutdown() -> None:
            nonlocal shutdown_task
            if shutdown_task is None or shutdown_task.done():
                shutdown_task = asyncio.create_task(update_server.stop())

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, request_shutdown)

        await update_server.start()
        await update_server.serve()

        if shutdown_task is not None:
            await shutdown_task
        return 0
    except Exception:
        return 1
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main() -> None:
    """Run the toy client using process environment variables."""

    raise SystemExit(asyncio.run(run_update_server()))


if __name__ == "__main__":
    main()
