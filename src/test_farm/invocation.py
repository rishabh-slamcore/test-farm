"""Baseline invocation execution."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal, TypedDict
from urllib import error, request

from test_farm.controller import start_controller_server
from test_farm.models import DEFAULT_BUNDLE, Bundle
from test_farm.subjects.update_server import start_update_server

RESULT_FILE_NAME_PATTERN = re.compile(r"result_(\d+)\.json$")
TIMED_OUT_ERROR_DETAIL = "No verified receipt received before timeout."
UPDATE_SERVER_BIND_ADDRESS = "127.0.0.1:8081"


class ClientStatus(StrEnum):
    """Supported client terminal outcomes for the current slice."""

    TIMED_OUT = "timed_out"
    SUCCESS = "success"


type InvocationStatus = Literal["success", "failed"]


@dataclass(frozen=True)
class ClientOutcome:
    """One client outcome recorded in a result file."""

    client_id: str
    client_status: ClientStatus
    bundle_id: str
    error_detail: str | None


class ClientOutcomePayload(TypedDict):
    """Serialized one-client result entry."""

    client_id: str
    client_status: ClientStatus
    bundle_id: str
    error_detail: str | None


class InvocationErrorPayload(TypedDict):
    """Serialized top-level invocation setup failure."""

    stage: str
    detail: str


class ResultFilePayload(TypedDict):
    """Serialized top-level result file payload."""

    invocation_instance: int
    scenario_file: str
    invocation_status: InvocationStatus
    started_at: str
    finished_at: str
    expected_bundle: dict[str, str | int] | None
    invocation_error: InvocationErrorPayload | None
    clients: list[ClientOutcomePayload]


async def execute_invocation(
    scenario_file: Path,
    client_count: int,
    controller_bind_address: str,
    receipt_timeout_seconds: int,
    results_dir: Path,
) -> tuple[Path, InvocationStatus]:
    """Execute the current invocation and write its Result File.

    :param scenario_file: Scenario file path supplied to the CLI.
    :param client_count: Number of clients requested by the scenario file.
    :param controller_bind_address: Bind address for the Controller Receipt Channel.
    :param receipt_timeout_seconds: Receipt wait duration before timing out.
    :param results_dir: Directory where result files are written.
    :returns: Written result file path and its invocation status.
    """

    invocation_instance = allocate_invocation_instance(results_dir)
    started_at = _utc_now()
    first_client_id = _client_id(1)

    async with start_update_server(bind_address=UPDATE_SERVER_BIND_ADDRESS) as update_server:
        try:
            expected_bundle = fetch_expected_bundle_from_update_server(
                update_server_base_url=update_server.base_url,
                bundle_id=DEFAULT_BUNDLE.bundle_id,
            )
        except RuntimeError as invocation_error:
            return (
                _write_failed_invocation_result(
                    results_dir=results_dir,
                    invocation_instance=invocation_instance,
                    scenario_file=scenario_file,
                    started_at=started_at,
                    invocation_error={
                        "stage": "manifest_fetch",
                        "detail": str(invocation_error),
                    },
                ),
                "failed",
            )

        async with start_controller_server(
            bind_address=controller_bind_address,
            invocation_instance=invocation_instance,
            client_id=first_client_id,
            expected_bundle=expected_bundle,
        ) as controller_server:
            first_client_succeeded = await controller_server.wait_for_valid_receipt(
                receipt_timeout_seconds
            )

    finished_at = _utc_now()
    client_outcomes = [
        _client_outcome(
            index=index,
            first_client_succeeded=first_client_succeeded,
            expected_bundle=expected_bundle,
        )
        for index in range(1, client_count + 1)
    ]
    invocation_status = _derive_invocation_status(client_outcomes)
    result_file = _write_result_file(
        results_dir=results_dir,
        payload=_result_file_payload(
            invocation_instance=invocation_instance,
            scenario_file=scenario_file,
            invocation_status=invocation_status,
            started_at=started_at,
            finished_at=finished_at,
            expected_bundle=expected_bundle.to_payload(),
            invocation_error=None,
            client_outcomes=client_outcomes,
        ),
    )
    return result_file, invocation_status


def _result_file_payload(
    *,
    invocation_instance: int,
    scenario_file: Path,
    invocation_status: InvocationStatus,
    started_at: str,
    finished_at: str,
    expected_bundle: dict[str, str | int] | None,
    invocation_error: InvocationErrorPayload | None,
    client_outcomes: list[ClientOutcome],
) -> ResultFilePayload:
    """Build the top-level result payload."""

    return {
        "invocation_instance": invocation_instance,
        "scenario_file": str(scenario_file),
        "invocation_status": invocation_status,
        "started_at": started_at,
        "finished_at": finished_at,
        "expected_bundle": expected_bundle,
        "invocation_error": invocation_error,
        "clients": [
            {
                "client_id": outcome.client_id,
                "client_status": outcome.client_status,
                "bundle_id": outcome.bundle_id,
                "error_detail": outcome.error_detail,
            }
            for outcome in client_outcomes
        ],
    }


def _write_failed_invocation_result(
    *,
    results_dir: Path,
    invocation_instance: int,
    scenario_file: Path,
    started_at: str,
    invocation_error: InvocationErrorPayload,
    expected_bundle: dict[str, str | int] | None = None,
) -> Path:
    """Persist a failed invocation result for setup-stage errors."""

    return _write_result_file(
        results_dir=results_dir,
        payload=_result_file_payload(
            invocation_instance=invocation_instance,
            scenario_file=scenario_file,
            invocation_status="failed",
            started_at=started_at,
            finished_at=_utc_now(),
            expected_bundle=expected_bundle,
            invocation_error=invocation_error,
            client_outcomes=[],
        ),
    )


def _write_result_file(*, results_dir: Path, payload: ResultFilePayload) -> Path:
    """Persist one invocation result payload to disk."""

    results_dir.mkdir(parents=True, exist_ok=True)
    result_file = results_dir / f"result_{payload['invocation_instance']}.json"
    result_file.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
    return result_file


def allocate_invocation_instance(results_dir: Path) -> int:
    """Allocate the next invocation_instance from existing result filenames.

    :param results_dir: Directory containing prior result files.
    :returns: Next invocation number, starting at 1.
    """

    highest_invocation_instance = 0

    if not results_dir.exists():
        return 1

    for result_file in results_dir.iterdir():
        match = RESULT_FILE_NAME_PATTERN.fullmatch(result_file.name)
        if match is None:
            continue
        highest_invocation_instance = max(highest_invocation_instance, int(match.group(1)))

    return highest_invocation_instance + 1


def _derive_invocation_status(client_outcomes: list[ClientOutcome]) -> InvocationStatus:
    """Derive invocation status from client outcomes.

    :param client_outcomes: Per-client terminal outcomes.
    :returns: Derived invocation status.
    """

    return (
        "success"
        if all(outcome.client_status == ClientStatus.SUCCESS for outcome in client_outcomes)
        else "failed"
    )


def fetch_expected_bundle_from_update_server(
    *, update_server_base_url: str, bundle_id: str
) -> Bundle:
    """Fetch expected bundle metadata from the Update Server manifest.

    :param update_server_base_url: Reachable Update Server base URL.
    :param bundle_id: Bundle identifier to request.
    :returns: Bundle metadata parsed from the manifest response.
    :raises RuntimeError: Raised when the manifest cannot be fetched or is invalid.
    """

    manifest_url = f"{update_server_base_url}/bundles/{bundle_id}/manifest"
    try:
        with request.urlopen(manifest_url) as response:
            payload = json.load(response)
    except (error.HTTPError, error.URLError, json.JSONDecodeError) as fetch_error:
        raise RuntimeError(
            f"Could not fetch expected bundle manifest from {manifest_url}."
        ) from fetch_error

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Expected Update Server manifest at {manifest_url} to be a JSON object."
        )

    manifest_bundle_id = payload.get("bundle_id")
    byte_count = payload.get("byte_count")
    checksum = payload.get("checksum")

    if not isinstance(manifest_bundle_id, str):
        raise RuntimeError(f"Manifest at {manifest_url} did not contain a string bundle_id.")

    if isinstance(byte_count, bool) or not isinstance(byte_count, int):
        raise RuntimeError(
            f"Manifest at {manifest_url} did not contain an integer byte_count."
        )

    if not isinstance(checksum, str):
        raise RuntimeError(f"Manifest at {manifest_url} did not contain a string checksum.")

    return Bundle(
        bundle_id=manifest_bundle_id,
        byte_count=byte_count,
        checksum=checksum,
    )


def _client_outcome(
    index: int, first_client_succeeded: bool, expected_bundle: Bundle
) -> ClientOutcome:
    """Build the client outcome supported by the current slice.

    :param index: One-based client index.
    :param first_client_succeeded: Whether the first expected client submitted a valid receipt.
    :param expected_bundle: Bundle metadata expected during the invocation.
    :returns: Recorded client outcome.
    """

    if index == 1 and first_client_succeeded:
        return ClientOutcome(
            client_id=_client_id(index),
            client_status=ClientStatus.SUCCESS,
            bundle_id=expected_bundle.bundle_id,
            error_detail=None,
        )

    return ClientOutcome(
        client_id=_client_id(index),
        client_status=ClientStatus.TIMED_OUT,
        bundle_id=expected_bundle.bundle_id,
        error_detail=TIMED_OUT_ERROR_DETAIL,
    )


def _client_id(index: int) -> str:
    """Create the stable client identifier for one client index.

    :param index: One-based client index.
    :returns: Stable client identifier.
    """

    return f"client-{index:03d}"


def _utc_now() -> str:
    """Return the current UTC timestamp as an ISO 8601 string.

    :returns: UTC timestamp with a trailing Z suffix.
    """

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
