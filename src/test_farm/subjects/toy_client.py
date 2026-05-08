"""Toy client workload for one baseline update flow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from os import environ
from typing import Mapping

import httpx

from test_farm.models import Bundle, ClientStatus

INVOCATION_INSTANCE_ENV = "TEST_FARM_INVOCATION_INSTANCE"
CLIENT_ID_ENV = "TEST_FARM_CLIENT_ID"
UPDATE_SERVER_URL_ENV = "TEST_FARM_UPDATE_SERVER_URL"
CONTROLLER_REPORTBACK_URL_ENV = "TEST_FARM_CONTROLLER_REPORTBACK_URL"
BUNDLE_ID_ENV = "TEST_FARM_BUNDLE_ID"


@dataclass(frozen=True)
class ToyClientResult:
    """Structured outcome for one toy-client execution."""

    client_status: ClientStatus
    bundle_id: str
    error_detail: str | None
    exit_code: int
    verified_bundle: Bundle | None = None


async def run_toy_client(environment: Mapping[str, str] | None = None) -> ToyClientResult:
    """Run the toy client against the configured Update Server and Controller.

    :param environment: Optional environment-variable mapping for tests.
    :returns: Structured toy-client terminal outcome.
    """

    resolved_environment = environ if environment is None else environment
    invocation_instance = int(resolved_environment[INVOCATION_INSTANCE_ENV])
    client_id = resolved_environment[CLIENT_ID_ENV]
    update_server_url = resolved_environment[UPDATE_SERVER_URL_ENV].rstrip("/")
    controller_reportback_url = resolved_environment[CONTROLLER_REPORTBACK_URL_ENV].rstrip("/")
    bundle_id = resolved_environment[BUNDLE_ID_ENV]

    async with httpx.AsyncClient() as client:
        try:
            manifest = await _fetch_bundle_manifest(
                client=client,
                update_server_url=update_server_url,
                bundle_id=bundle_id,
            )
            bundle_bytes = await _fetch_bundle_bytes(
                client=client,
                update_server_url=update_server_url,
                bundle_id=bundle_id,
            )
        except (OSError, ValueError) as fetch_error:
            return ToyClientResult(
                client_status=ClientStatus.DOWNLOAD_FAILED,
                bundle_id=bundle_id,
                error_detail=str(fetch_error),
                exit_code=1,
            )

        verified_bundle = Bundle.from_bytes(bundle_id=bundle_id, bundle_bytes=bundle_bytes)

        if verified_bundle != manifest:
            return ToyClientResult(
                client_status=ClientStatus.CHECKSUM_MISMATCH,
                bundle_id=bundle_id,
                error_detail="Downloaded bundle did not match the manifest.",
                exit_code=2,
                verified_bundle=verified_bundle,
            )

        try:
            await _post_receipt(
                client=client,
                controller_reportback_url=controller_reportback_url,
                invocation_instance=invocation_instance,
                client_id=client_id,
                bundle=verified_bundle,
            )
        except (OSError, ValueError) as receipt_error:
            return ToyClientResult(
                client_status=ClientStatus.RECEIPT_REJECTED,
                bundle_id=bundle_id,
                error_detail=str(receipt_error),
                exit_code=3,
                verified_bundle=verified_bundle,
            )

    return ToyClientResult(
        client_status=ClientStatus.SUCCESS,
        bundle_id=bundle_id,
        error_detail=None,
        exit_code=0,
        verified_bundle=verified_bundle,
    )


async def _fetch_bundle_manifest(
    *, client: httpx.AsyncClient, update_server_url: str, bundle_id: str
) -> Bundle:
    payload = await _read_json(
        client,
        f"{update_server_url}/bundles/{bundle_id}/manifest",
    )
    manifest_bundle_id = payload.get("bundle_id")
    byte_count = payload.get("byte_count")
    checksum = payload.get("checksum")

    if not isinstance(manifest_bundle_id, str):
        raise ValueError("Manifest did not include a string bundle_id.")

    if isinstance(byte_count, bool) or not isinstance(byte_count, int):
        raise ValueError("Manifest did not include an integer byte_count.")

    if not isinstance(checksum, str):
        raise ValueError("Manifest did not include a string checksum.")

    return Bundle(
        bundle_id=manifest_bundle_id,
        byte_count=byte_count,
        checksum=checksum,
    )


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
    bundle: Bundle,
) -> None:
    body = json.dumps(bundle.to_payload()).encode("utf-8")
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


async def _read_json(client: httpx.AsyncClient, url: str) -> dict[str, object]:
    payload = json.loads((await _read_bytes(client, url)).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object from {url}.")
    return payload


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
