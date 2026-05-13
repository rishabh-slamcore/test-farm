"""Toy client behavior tests."""

import asyncio
import json
from collections.abc import Callable

import httpx
from pytest import MonkeyPatch

from test_farm.controller import ClientOutcome, start_controller_server
from test_farm.identifiers import expected_client_ids
from test_farm.models import DEFAULT_BUNDLE, Bundle, ClientStatus
from test_farm.subjects.toy_client import run_toy_client
from test_farm.subjects.update_server import start_update_server

HTTPX_ASYNC_CLIENT = httpx.AsyncClient


def test_toy_client_downloads_bundle_and_posts_success_receipt(
    bind_address_factory: Callable[[], str],
) -> None:
    exit_code, client_outcomes = asyncio.run(_run_successful_client(bind_address_factory))

    assert exit_code == 0
    assert client_outcomes["client-001"].client_status == ClientStatus.SUCCESS
    assert client_outcomes["client-001"].bundle_id == DEFAULT_BUNDLE.bundle_id
    assert client_outcomes["client-001"].error_detail is None


def test_toy_client_posts_download_failed_receipt_when_bundle_fetch_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_receipt_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(
        "test_farm.subjects.toy_client.httpx.AsyncClient",
        _async_client_factory(
            _transport_for_download_failure(
                observed_receipt_payloads=observed_receipt_payloads
            )
        ),
    )

    exit_code = asyncio.run(run_toy_client(_toy_client_environment()))

    assert exit_code == 1
    assert observed_receipt_payloads == [
        {
            "client_status": "download_failed",
            "error_detail": (
                "Request to http://update-server.example/bundles/baseline failed: "
                "connection refused"
            ),
        }
    ]


def test_toy_client_posts_reported_bundle_derived_from_downloaded_bytes(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_receipt_payloads: list[dict[str, object]] = []
    bundle_bytes = b"corrupted bundle bytes\n"
    monkeypatch.setattr(
        "test_farm.subjects.toy_client.httpx.AsyncClient",
        _async_client_factory(
            _transport_for_bundle_success(
                bundle_bytes=bundle_bytes,
                observed_receipt_payloads=observed_receipt_payloads,
            )
        ),
    )

    exit_code = asyncio.run(run_toy_client(_toy_client_environment()))

    assert exit_code == 0
    assert observed_receipt_payloads == [
        {
            "client_status": "success",
            "reported_bundle": Bundle.from_bytes(
                bundle_id=DEFAULT_BUNDLE.bundle_id,
                bundle_bytes=bundle_bytes,
            ).to_payload(),
        }
    ]


def test_toy_client_returns_non_zero_when_receipt_post_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "test_farm.subjects.toy_client.httpx.AsyncClient",
        _async_client_factory(_transport_for_receipt_rejection()),
    )

    exit_code = asyncio.run(run_toy_client(_toy_client_environment()))

    assert exit_code == 2


async def _run_successful_client(
    bind_address_factory: Callable[[], str],
) -> tuple[int, dict[str, ClientOutcome]]:
    update_server_bind_address = bind_address_factory()
    controller_bind_address = bind_address_factory()

    async with start_update_server(bind_address=update_server_bind_address) as update_server:
        async with start_controller_server(
            bind_address=controller_bind_address,
            invocation_instance=7,
            expected_client_ids=expected_client_ids(1),
            expected_bundle=DEFAULT_BUNDLE,
        ) as controller_server:
            exit_code = await run_toy_client(
                {
                    "TEST_FARM_INVOCATION_INSTANCE": "7",
                    "TEST_FARM_CLIENT_ID": "client-001",
                    "TEST_FARM_UPDATE_SERVER_URL": update_server.base_url,
                    "TEST_FARM_CONTROLLER_REPORTBACK_URL": f"http://{controller_bind_address}",
                    "TEST_FARM_BUNDLE_ID": DEFAULT_BUNDLE.bundle_id,
                },
            )
            all_outcomes_recorded = await controller_server.wait_for_client_outcomes(
                timeout_seconds=1
            )

    assert all_outcomes_recorded is True
    return exit_code, controller_server.client_outcomes


def _toy_client_environment() -> dict[str, str]:
    return {
        "TEST_FARM_INVOCATION_INSTANCE": "7",
        "TEST_FARM_CLIENT_ID": "client-001",
        "TEST_FARM_UPDATE_SERVER_URL": "http://update-server.example",
        "TEST_FARM_CONTROLLER_REPORTBACK_URL": "http://controller.example",
        "TEST_FARM_BUNDLE_ID": DEFAULT_BUNDLE.bundle_id,
    }


def _transport_for_download_failure(
    *, observed_receipt_payloads: list[dict[str, object]]
) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(f"/bundles/{DEFAULT_BUNDLE.bundle_id}"):
            raise httpx.ConnectError("connection refused", request=request)
        if url.endswith("/receipt"):
            observed_receipt_payloads.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(202, json={"status": "accepted"})
        raise AssertionError(f"Unexpected URL: {url}")

    return httpx.MockTransport(_handler)


def _transport_for_bundle_success(
    *, bundle_bytes: bytes, observed_receipt_payloads: list[dict[str, object]]
) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(f"/bundles/{DEFAULT_BUNDLE.bundle_id}"):
            return httpx.Response(200, content=bundle_bytes)
        if url.endswith("/receipt"):
            observed_receipt_payloads.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(202, json={"status": "accepted"})
        raise AssertionError(f"Unexpected URL: {url}")

    return httpx.MockTransport(_handler)


def _transport_for_receipt_rejection() -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(f"/bundles/{DEFAULT_BUNDLE.bundle_id}"):
            return httpx.Response(200, content=DEFAULT_BUNDLE.bundle_id.encode("utf-8"))
        if url.endswith("/receipt"):
            return httpx.Response(410, json={"status": "rejected"})
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
