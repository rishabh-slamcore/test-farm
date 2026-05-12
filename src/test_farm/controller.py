"""Controller receipt-channel behavior for baseline invocations."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from test_farm.models import Bundle, ClientStatus, Receipt


@dataclass(frozen=True)
class ControllerResponse:
    """HTTP-style response returned by controller state."""

    status_code: int
    body: dict[str, object]


@dataclass(frozen=True)
class ClientOutcome:
    """Controller-owned per-client outcome for one invocation."""

    client_id: str
    client_status: ClientStatus
    bundle_id: str
    error_detail: str | None
    reported_bundle: Bundle | None = None


class ControllerState:
    """In-memory Controller Receipt Channel state and validation logic."""

    def __init__(
        self,
        *,
        invocation_instance: int,
        expected_client_ids: tuple[str, ...],
        expected_bundle: Bundle,
    ) -> None:
        self._invocation_instance = invocation_instance
        self._expected_client_ids = expected_client_ids
        self._expected_bundle = expected_bundle
        self._client_outcomes: dict[str, ClientOutcome] = {}
        self._all_client_outcomes_event = asyncio.Event()
        self._receipt_channel_open = True
        self._lock = asyncio.Lock()

    @property
    def expected_client_ids(self) -> tuple[str, ...]:
        """Return deterministic Client IDs for this invocation."""

        return self._expected_client_ids

    @property
    def client_outcomes(self) -> dict[str, ClientOutcome]:
        """Return controller-owned client outcomes keyed by Client ID."""

        return dict(self._client_outcomes)

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

        posted_receipt = _parse_receipt_payload(body)
        if posted_receipt is None:
            return ControllerResponse(
                status_code=400,
                body={"status": "rejected", "detail": "Receipt body must be a JSON object."},
            )

        mismatch_detail = self._mismatch_detail(
            route_invocation_instance=route_invocation_instance,
            route_client_id=route_client_id,
        )
        if mismatch_detail is not None:
            return ControllerResponse(
                status_code=409,
                body={"status": "rejected", "detail": mismatch_detail},
            )

        latest_outcome = self._normalize_outcome(
            route_client_id=route_client_id,
            posted_receipt=posted_receipt,
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

            existing_outcome = self._client_outcomes.get(route_client_id)
            if (
                existing_outcome is None
                or existing_outcome.client_status != ClientStatus.SUCCESS
            ):
                self._client_outcomes[route_client_id] = latest_outcome

            if len(self._client_outcomes) == len(self._expected_client_ids):
                self._receipt_channel_open = False
                self._all_client_outcomes_event.set()

        return ControllerResponse(status_code=202, body={"status": "accepted"})

    async def wait_for_client_outcomes(self, timeout_seconds: float) -> bool:
        """Wait for every expected client outcome for this invocation.

        :param timeout_seconds: Receipt wait timeout in seconds.
        :returns: ``True`` when every expected outcome is recorded, else ``False``.
        """

        try:
            await asyncio.wait_for(
                self._all_client_outcomes_event.wait(), timeout=timeout_seconds
            )
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
    ) -> str | None:
        if route_invocation_instance != self._invocation_instance:
            return "Receipt invocation_instance did not match the current invocation."

        if route_client_id not in self._expected_client_ids:
            return "Receipt client_id did not match an expected client."

        return None

    def _normalize_outcome(
        self, *, route_client_id: str, posted_receipt: Receipt
    ) -> ClientOutcome:
        if posted_receipt.client_status == "download_failed":
            return ClientOutcome(
                client_id=route_client_id,
                client_status=ClientStatus.DOWNLOAD_FAILED,
                bundle_id=self._expected_bundle.bundle_id,
                error_detail=posted_receipt.error_detail,
            )

        reported_bundle = posted_receipt.reported_bundle
        if reported_bundle is None:
            raise AssertionError("Success receipt must include reported bundle metadata.")

        mismatch_detail = _bundle_mismatch_detail(
            expected_bundle=self._expected_bundle,
            reported_bundle=reported_bundle,
        )
        if mismatch_detail is None:
            return ClientOutcome(
                client_id=route_client_id,
                client_status=ClientStatus.SUCCESS,
                bundle_id=self._expected_bundle.bundle_id,
                error_detail=None,
            )

        return ClientOutcome(
            client_id=route_client_id,
            client_status=ClientStatus.CHECKSUM_MISMATCH,
            bundle_id=self._expected_bundle.bundle_id,
            error_detail=mismatch_detail,
            reported_bundle=reported_bundle,
        )


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

    @property
    def expected_client_ids(self) -> tuple[str, ...]:
        """Return deterministic Client IDs for this invocation."""

        return self._state.expected_client_ids

    @property
    def client_outcomes(self) -> dict[str, ClientOutcome]:
        """Return controller-owned client outcomes keyed by Client ID."""

        return self._state.client_outcomes

    async def wait_for_client_outcomes(self, timeout_seconds: float) -> bool:
        """Wait for every expected client outcome in the wrapped state."""

        return await self._state.wait_for_client_outcomes(timeout_seconds)

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
    expected_client_ids: tuple[str, ...],
    expected_bundle: Bundle,
) -> ControllerServer:
    """Create the Controller server for one invocation's expected client receipts."""

    state = ControllerState(
        invocation_instance=invocation_instance,
        expected_client_ids=expected_client_ids,
        expected_bundle=expected_bundle,
    )
    return ControllerServer(bind_address=bind_address, state=state)


def _parse_receipt_payload(body: bytes | None) -> Receipt | None:
    if body is None:
        return None

    try:
        parsed_body = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(parsed_body, dict):
        return None

    client_status = parsed_body.get("client_status")
    reported_bundle_payload = parsed_body.get("reported_bundle")
    error_detail = parsed_body.get("error_detail")

    if client_status == "success":
        if error_detail is not None:
            return None
        reported_bundle = _parse_bundle_payload(reported_bundle_payload)
        if reported_bundle is None:
            return None
        return Receipt(
            client_status="success",
            reported_bundle=reported_bundle,
            error_detail=None,
        )

    if client_status == "download_failed":
        if not isinstance(error_detail, str):
            return None
        if reported_bundle_payload is not None:
            return None
        return Receipt(
            client_status="download_failed",
            reported_bundle=None,
            error_detail=error_detail,
        )

    return None


def _parse_bundle_payload(payload: object) -> Bundle | None:
    if not isinstance(payload, dict):
        return None

    bundle_id = payload.get("bundle_id")
    byte_count = payload.get("byte_count")
    checksum = payload.get("checksum")

    if not isinstance(bundle_id, str):
        return None

    if isinstance(byte_count, bool) or not isinstance(byte_count, int):
        return None

    if not isinstance(checksum, str):
        return None

    return Bundle(bundle_id=bundle_id, byte_count=byte_count, checksum=checksum)


def _bundle_mismatch_detail(*, expected_bundle: Bundle, reported_bundle: Bundle) -> str | None:
    if reported_bundle.bundle_id != expected_bundle.bundle_id:
        return "Receipt bundle_id did not match the expected bundle."

    if reported_bundle.byte_count != expected_bundle.byte_count:
        return "Receipt byte_count did not match the expected bundle."

    if reported_bundle.checksum != expected_bundle.checksum:
        return "Receipt checksum did not match the expected bundle."

    return None


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
