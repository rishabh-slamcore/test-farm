"""Toy client behavior tests."""

import asyncio
import socket
from collections.abc import Callable

import httpx
from pytest import MonkeyPatch

from test_farm.controller import start_controller_server
from test_farm.models import DEFAULT_BUNDLE, ClientStatus
from test_farm.subjects.toy_client import ToyClientResult, run_toy_client
from test_farm.subjects.update_server import start_update_server

HTTPX_ASYNC_CLIENT = httpx.AsyncClient


def test_toy_client_fetches_bundle_verifies_it_and_posts_receipt() -> None:
    result = asyncio.run(_run_successful_client())

    assert result.client_status == ClientStatus.SUCCESS
    assert result.bundle_id == DEFAULT_BUNDLE.bundle_id
    assert result.error_detail is None
    assert result.exit_code == 0
    assert result.verified_bundle == DEFAULT_BUNDLE


def test_toy_client_reports_download_failure_when_bundle_fetch_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "test_farm.subjects.toy_client.httpx.AsyncClient",
        _async_client_factory(_raising_transport("connection refused")),
    )

    result = asyncio.run(run_toy_client(_toy_client_environment()))

    assert result.client_status == ClientStatus.DOWNLOAD_FAILED
    assert result.bundle_id == DEFAULT_BUNDLE.bundle_id
    assert result.error_detail is not None
    assert (
        "Request to http://update-server.example/bundles/baseline/manifest failed:"
        in result.error_detail
    )
    assert "connection refused" in result.error_detail
    assert result.exit_code == 1
    assert result.verified_bundle is None


def test_toy_client_reports_checksum_mismatch_when_bundle_differs_from_manifest(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "test_farm.subjects.toy_client.httpx.AsyncClient",
        _async_client_factory(
            _transport_for_payloads(
                manifest_payload=DEFAULT_BUNDLE.to_payload(),
                bundle_bytes=b"corrupted bundle bytes\n",
            )
        ),
    )

    result = asyncio.run(run_toy_client(_toy_client_environment()))

    assert result.client_status == ClientStatus.CHECKSUM_MISMATCH
    assert result.bundle_id == DEFAULT_BUNDLE.bundle_id
    assert result.error_detail == "Downloaded bundle did not match the manifest."
    assert result.exit_code == 2
    assert result.verified_bundle is not None
    assert result.verified_bundle != DEFAULT_BUNDLE


def test_toy_client_reports_receipt_rejection_when_controller_rejects_post() -> None:
    result = asyncio.run(_run_rejected_client())

    assert result.client_status == ClientStatus.RECEIPT_REJECTED
    assert result.bundle_id == DEFAULT_BUNDLE.bundle_id
    assert result.error_detail is not None
    assert "failed with HTTP 409" in result.error_detail
    assert result.exit_code == 3
    assert result.verified_bundle == DEFAULT_BUNDLE


async def _run_successful_client() -> ToyClientResult:
    update_server_bind_address = _allocate_bind_address()
    controller_bind_address = _allocate_bind_address()

    async with start_update_server(bind_address=update_server_bind_address) as update_server:
        async with start_controller_server(
            bind_address=controller_bind_address,
            invocation_instance=7,
            client_id="client-001",
            expected_bundle=DEFAULT_BUNDLE,
        ) as controller_server:
            result = await run_toy_client(
                {
                    "TEST_FARM_INVOCATION_INSTANCE": "7",
                    "TEST_FARM_CLIENT_ID": "client-001",
                    "TEST_FARM_UPDATE_SERVER_URL": update_server.base_url,
                    "TEST_FARM_CONTROLLER_REPORTBACK_URL": f"http://{controller_bind_address}",
                    "TEST_FARM_BUNDLE_ID": DEFAULT_BUNDLE.bundle_id,
                },
            )
            receipt_accepted = await controller_server.wait_for_valid_receipt(
                timeout_seconds=1
            )

    assert receipt_accepted is True
    return result


async def _run_rejected_client() -> ToyClientResult:
    update_server_bind_address = _allocate_bind_address()
    controller_bind_address = _allocate_bind_address()

    async with start_update_server(bind_address=update_server_bind_address) as update_server:
        async with start_controller_server(
            bind_address=controller_bind_address,
            invocation_instance=7,
            client_id="client-999",
            expected_bundle=DEFAULT_BUNDLE,
        ):
            return await run_toy_client(
                {
                    "TEST_FARM_INVOCATION_INSTANCE": "7",
                    "TEST_FARM_CLIENT_ID": "client-001",
                    "TEST_FARM_UPDATE_SERVER_URL": update_server.base_url,
                    "TEST_FARM_CONTROLLER_REPORTBACK_URL": f"http://{controller_bind_address}",
                    "TEST_FARM_BUNDLE_ID": DEFAULT_BUNDLE.bundle_id,
                },
            )


def _allocate_bind_address() -> str:
    # Tests start real local servers, so each one needs an available localhost
    # port without relying on a hardcoded value that may already be in use.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        host, port = server_socket.getsockname()

    return f"{host}:{port}"


def _toy_client_environment() -> dict[str, str]:
    return {
        "TEST_FARM_INVOCATION_INSTANCE": "7",
        "TEST_FARM_CLIENT_ID": "client-001",
        "TEST_FARM_UPDATE_SERVER_URL": "http://update-server.example",
        "TEST_FARM_CONTROLLER_REPORTBACK_URL": "http://controller.example",
        "TEST_FARM_BUNDLE_ID": DEFAULT_BUNDLE.bundle_id,
    }


def _raising_transport(detail: str) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(detail, request=request)

    return httpx.MockTransport(_handler)


def _transport_for_payloads(
    *, manifest_payload: dict[str, str | int], bundle_bytes: bytes
) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/manifest"):
            return httpx.Response(200, json=manifest_payload)
        if url.endswith(f"/bundles/{DEFAULT_BUNDLE.bundle_id}"):
            return httpx.Response(200, content=bundle_bytes)
        raise AssertionError(f"Unexpected URL: {url}")

    return httpx.MockTransport(_handler)


def _async_client_factory(
    transport: httpx.AsyncBaseTransport,
) -> Callable[..., httpx.AsyncClient]:
    def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        del args
        del kwargs
        return HTTPX_ASYNC_CLIENT(transport=transport)

    return _factory
