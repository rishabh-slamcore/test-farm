"""Toy client workload for one baseline update flow."""

from __future__ import annotations

import json
from os import environ
from typing import Mapping

import httpx

from test_farm.models import Bundle, Receipt

INVOCATION_INSTANCE_ENV = "TEST_FARM_INVOCATION_INSTANCE"
CLIENT_ID_ENV = "TEST_FARM_CLIENT_ID"
UPDATE_SERVER_URL_ENV = "TEST_FARM_UPDATE_SERVER_URL"
CONTROLLER_REPORTBACK_URL_ENV = "TEST_FARM_CONTROLLER_REPORTBACK_URL"
BUNDLE_ID_ENV = "TEST_FARM_BUNDLE_ID"


async def run_toy_client(environment: Mapping[str, str] | None = None) -> int:
    """Run the toy client against the configured Update Server and Controller.

    :param environment: Optional environment-variable mapping for tests.
    :returns: Process-style exit code for the toy client attempt.
    """

    resolved_environment = environ if environment is None else environment
    invocation_instance = int(resolved_environment[INVOCATION_INSTANCE_ENV])
    client_id = resolved_environment[CLIENT_ID_ENV]
    update_server_url = resolved_environment[UPDATE_SERVER_URL_ENV].rstrip("/")
    controller_reportback_url = resolved_environment[CONTROLLER_REPORTBACK_URL_ENV].rstrip("/")
    bundle_id = resolved_environment[BUNDLE_ID_ENV]

    async with httpx.AsyncClient() as client:
        try:
            bundle_bytes = await _fetch_bundle_bytes(
                client=client,
                update_server_url=update_server_url,
                bundle_id=bundle_id,
            )
        except OSError as fetch_error:
            try:
                await _post_receipt(
                    client=client,
                    controller_reportback_url=controller_reportback_url,
                    invocation_instance=invocation_instance,
                    client_id=client_id,
                    receipt=Receipt(
                        client_status="download_failed",
                        reported_bundle=None,
                        error_detail=str(fetch_error),
                    ),
                )
            except OSError:
                return 2
            return 1

        reported_bundle = Bundle.from_bytes(bundle_id=bundle_id, bundle_bytes=bundle_bytes)
        try:
            await _post_receipt(
                client=client,
                controller_reportback_url=controller_reportback_url,
                invocation_instance=invocation_instance,
                client_id=client_id,
                receipt=Receipt(
                    client_status="success",
                    reported_bundle=reported_bundle,
                    error_detail=None,
                ),
            )
        except OSError:
            return 2

    return 0


async def _fetch_bundle_bytes(
    *, client: httpx.AsyncClient, update_server_url: str, bundle_id: str
) -> bytes:
    return await _read_bytes(client, f"{update_server_url}/bundles/{bundle_id}")


async def _post_receipt(
    *,
    client: httpx.AsyncClient,
    controller_reportback_url: str,
    invocation_instance: int,
    client_id: str,
    receipt: Receipt,
) -> None:
    body = json.dumps(receipt.to_payload()).encode("utf-8")
    try:
        response = await client.post(
            (
                f"{controller_reportback_url}/invocations/"
                f"{invocation_instance}/clients/{client_id}/receipt"
            ),
            content=body,
            headers={"content-type": "application/json"},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as response_error:
        detail = response_error.response.text
        raise OSError(
            "Receipt post to "
            f"{controller_reportback_url} failed with HTTP "
            f"{response_error.response.status_code}: {detail}"
        ) from response_error
    except httpx.HTTPError as response_error:
        raise OSError(
            f"Receipt post to {controller_reportback_url} failed: {response_error}"
        ) from response_error


async def _read_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    try:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.content
        if not isinstance(payload, bytes):
            raise ValueError(f"Expected byte payload from {url}.")
        return payload
    except httpx.HTTPStatusError as response_error:
        detail = response_error.response.text
        raise OSError(
            f"Request to {url} failed with HTTP {response_error.response.status_code}: {detail}"
        ) from response_error
    except httpx.HTTPError as response_error:
        raise OSError(f"Request to {url} failed: {response_error}") from response_error
