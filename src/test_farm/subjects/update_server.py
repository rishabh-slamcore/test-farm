"""Toy Update Server for baseline bundle delivery."""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from test_farm.models import DEFAULT_BUNDLE, DEFAULT_BUNDLE_BYTES

UPDATE_SERVER_BIND_ADDRESS_ENV = "TEST_FARM_UPDATE_SERVER_BIND_ADDRESS"


def create_update_server_app() -> FastAPI:
    """Build the Update Server application."""

    app = FastAPI()

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(status_code=200, content={"status": "ok"})

    @app.get("/bundles/{bundle_id}/manifest")
    async def manifest(bundle_id: str) -> JSONResponse:
        if bundle_id != DEFAULT_BUNDLE.bundle_id:
            return JSONResponse(
                status_code=404,
                content={"detail": f"Bundle {bundle_id} was not found."},
            )
        return JSONResponse(status_code=200, content=DEFAULT_BUNDLE.to_payload())

    @app.get("/bundles/{bundle_id}")
    async def bundle(bundle_id: str) -> Response:
        if bundle_id != DEFAULT_BUNDLE.bundle_id:
            return JSONResponse(
                status_code=404,
                content={"detail": f"Bundle {bundle_id} was not found."},
            )
        return Response(
            content=DEFAULT_BUNDLE_BYTES,
            status_code=200,
            media_type="application/octet-stream",
        )

    return app


class UpdateServer:
    """Async uvicorn wrapper around the toy Update Server app."""

    def __init__(self, *, bind_address: str) -> None:
        host, port = _parse_bind_address(bind_address)
        self.base_url = f"http://{host}:{port}"
        self._server = uvicorn.Server(
            uvicorn.Config(
                app=create_update_server_app(),
                host=host,
                port=port,
                log_level="warning",
                access_log=False,
            )
        )
        self._server_task: asyncio.Task[None] | None = None

    async def start(self) -> "UpdateServer":
        if self._server_task is not None:
            return self
        self._server_task = asyncio.create_task(self._serve())
        await self._wait_until_started()
        return self

    async def stop(self) -> None:
        self._server.should_exit = True
        if self._server_task is not None:
            await self._server_task

    async def _serve(self) -> None:
        await self._server.serve()

    async def _wait_until_started(self) -> None:
        while not self._server.started:
            if self._server_task is not None and self._server_task.done():
                await self._server_task
                raise RuntimeError("Update Server stopped before becoming ready.")
            await asyncio.sleep(0.01)

    async def serve(self) -> None:
        if self._server_task is None:
            raise RuntimeError("Server has not been started")
        await self._server_task

    async def __aenter__(self) -> "UpdateServer":
        return await self.start()

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type
        del exc
        del traceback
        await self.stop()


def start_update_server(*, bind_address: str) -> UpdateServer:
    """Create the Update Server for one invocation.

    :param bind_address: Bind address in ``host:port`` form.
    :returns: Running Update Server wrapper.
    """

    return UpdateServer(bind_address=bind_address)


def _parse_bind_address(bind_address: str) -> tuple[str, int]:
    host, separator, port_text = bind_address.rpartition(":")
    if separator == "" or host == "":
        raise ValueError(f"Update Server bind address must be host:port, got {bind_address}.")

    try:
        port = int(port_text)
    except ValueError as error:
        raise ValueError(
            f"Update Server bind address must end with an integer port, got {bind_address}."
        ) from error

    if port < 0 or port > 65535:
        raise ValueError(
            f"Update Server bind address port must be between 0 and 65535, got {port}."
        )

    return host, port
