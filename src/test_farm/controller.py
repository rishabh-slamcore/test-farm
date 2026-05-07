"""Controller receipt-channel behavior for baseline invocations."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from test_farm.models import Bundle


@dataclass(frozen=True)
class ControllerResponse:
    """HTTP-style response returned by controller state."""

    status_code: int
    body: dict[str, object]


class ControllerState:
    """In-memory Controller Receipt Channel state and validation logic."""

    def __init__(
        self,
        *,
        invocation_instance: int,
        client_id: str,
        expected_bundle: Bundle,
    ) -> None:
        self._invocation_instance = invocation_instance
        self._client_id = client_id
        self._expected_bundle = expected_bundle
        self._receipt_event = asyncio.Event()
        self._receipt_channel_open = True
        self._lock = asyncio.Lock()

    async def handle_receipt(
        self,
        *,
        route_invocation_instance: int,
        route_client_id: str,
        body: bytes | None,
    ) -> ControllerResponse:
        """Validate and record one submitted receipt.

        :param route_invocation_instance: Invocation instance from the request path.
        :param route_client_id: Client id from the request path.
        :param body: Raw request body bytes, if any.
        :returns: HTTP-style response payload.
        """

        async with self._lock:
            if not self._receipt_channel_open:
                return ControllerResponse(
                    status_code=410,
                    body={
                        "status": "rejected",
                        "detail": "Receipt channel is closed for this invocation.",
                    },
                )

        payload = _parse_receipt_payload(body)
        if payload is None:
            return ControllerResponse(
                status_code=400,
                body={"status": "rejected", "detail": "Receipt body must be a JSON object."},
            )

        mismatch_detail = self._mismatch_detail(
            route_invocation_instance=route_invocation_instance,
            route_client_id=route_client_id,
            receipt_bundle=payload,
        )
        if mismatch_detail is not None:
            return ControllerResponse(
                status_code=409,
                body={"status": "rejected", "detail": mismatch_detail},
            )

        async with self._lock:
            if not self._receipt_channel_open:
                return ControllerResponse(
                    status_code=410,
                    body={
                        "status": "rejected",
                        "detail": "Receipt channel is closed for this invocation.",
                    },
                )
            self._receipt_channel_open = False
            self._receipt_event.set()

        return ControllerResponse(status_code=202, body={"status": "accepted"})

    async def wait_for_valid_receipt(self, timeout_seconds: int) -> bool:
        """Wait for the one valid receipt expected by this slice.

        :param timeout_seconds: Receipt wait timeout.
        :returns: ``True`` when a valid receipt is accepted, else ``False``.
        """

        try:
            await asyncio.wait_for(self._receipt_event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            async with self._lock:
                self._receipt_channel_open = False
            return False

        return True

    def _mismatch_detail(
        self,
        *,
        route_invocation_instance: int,
        route_client_id: str,
        receipt_bundle: Bundle,
    ) -> str | None:
        if route_invocation_instance != self._invocation_instance:
            return "Receipt invocation_instance did not match the current invocation."

        if route_client_id != self._client_id:
            return "Receipt client_id did not match the expected client."

        if receipt_bundle.bundle_id != self._expected_bundle.bundle_id:
            return "Receipt bundle_id did not match the expected bundle."

        if receipt_bundle.byte_count != self._expected_bundle.byte_count:
            return "Receipt byte_count did not match the expected bundle."

        if receipt_bundle.checksum != self._expected_bundle.checksum:
            return "Receipt checksum did not match the expected bundle."

        return None


def create_controller_app(state: ControllerState) -> FastAPI:
    """Build the FastAPI application for one controller receipt channel."""

    app = FastAPI()

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(status_code=200, content={"status": "ok"})

    @app.post("/invocations/{invocation_instance}/clients/{client_id}/receipt")
    async def receipt(
        invocation_instance: int, client_id: str, request: Request
    ) -> JSONResponse:
        response = await state.handle_receipt(
            route_invocation_instance=invocation_instance,
            route_client_id=client_id,
            body=await request.body(),
        )
        return JSONResponse(status_code=response.status_code, content=response.body)

    return app


class ControllerServer:
    """Async uvicorn wrapper around a FastAPI controller app."""

    def __init__(self, *, bind_address: str, state: ControllerState) -> None:
        host, port = _parse_bind_address(bind_address)
        self._state = state
        self._server = uvicorn.Server(
            uvicorn.Config(
                app=create_controller_app(state),
                host=host,
                port=port,
                log_level="warning",
                access_log=False,
            )
        )
        self._server_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> ControllerServer:
        self._server_task = asyncio.create_task(self._serve())
        # Wait till uvicorn server signals its ready to accept requests
        await self._wait_until_started()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type
        del exc
        del traceback
        ## uvicorn server periodically checks this flag.
        self._server.should_exit = True
        if self._server_task is not None:
            await self._server_task

    async def wait_for_valid_receipt(self, timeout_seconds: int) -> bool:
        """Wait for the one valid receipt expected by the wrapped state."""

        return await self._state.wait_for_valid_receipt(timeout_seconds)

    async def _serve(self) -> None:
        await self._server.serve()

    async def _wait_until_started(self) -> None:
        while not self._server.started:
            if self._server_task is not None and self._server_task.done():
                await self._server_task
                raise RuntimeError("Controller server stopped before becoming ready.")
            await asyncio.sleep(0.01)


def start_controller_server(
    *,
    bind_address: str,
    invocation_instance: int,
    client_id: str,
    expected_bundle: Bundle,
) -> ControllerServer:
    """Create the Controller server for one expected client receipt."""

    state = ControllerState(
        invocation_instance=invocation_instance,
        client_id=client_id,
        expected_bundle=expected_bundle,
    )
    return ControllerServer(bind_address=bind_address, state=state)


def _parse_receipt_payload(body: bytes | None) -> Bundle | None:
    if body is None:
        return None

    try:
        parsed_body = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(parsed_body, dict):
        return None

    bundle_id = parsed_body.get("bundle_id")
    byte_count = parsed_body.get("byte_count")
    checksum = parsed_body.get("checksum")

    if not isinstance(bundle_id, str):
        return None

    if isinstance(byte_count, bool) or not isinstance(byte_count, int):
        return None

    if not isinstance(checksum, str):
        return None

    return Bundle(bundle_id=bundle_id, byte_count=byte_count, checksum=checksum)


def _parse_bind_address(bind_address: str) -> tuple[str, int]:
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

    return host, port
