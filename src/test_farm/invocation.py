"""Baseline invocation execution."""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping, TypedDict, cast

import httpx

from test_farm.controller import ControllerServer, start_controller_server
from test_farm.identifiers import expected_client_ids as build_expected_client_ids
from test_farm.identifiers import invocation_directory_name
from test_farm.models import (
    DEFAULT_BUNDLE,
    Bundle,
    BundlePayload,
    ClientOutcome,
    ClientOutcomePayload,
    ClientStatus,
)
from test_farm.runtime.invocation.factory import create_default_invocation_runner
from test_farm.runtime.invocation_protocol import (
    InvocationRunner,
    InvocationSession,
    RuntimeSetupError,
)
from test_farm.runtime.networking import (
    derive_update_server_bind_address,
    parse_reachable_service_endpoint,
    service_url,
)
from test_farm.scenario import Scenario
from test_farm.subjects.update_server import start_update_server

RESULT_FILE_NAME_PATTERN = re.compile(r"result_(\d+)\.json$")
INVOCATION_DIRECTORY_NAME_PATTERN = re.compile(r"(\d+)$")
TIMED_OUT_ERROR_DETAIL = "No receipt received before timeout."


type InvocationStatus = Literal["success", "failed"]


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
    expected_bundle: BundlePayload | None
    invocation_error: InvocationErrorPayload | None
    clients: list[ClientOutcomePayload]


async def execute_invocation(
    scenario: Scenario,
    controller_bind_address: str,
    results_dir: Path,
    invocation_runner: InvocationRunner | None = None,
    keep_containers: bool = False,
) -> tuple[Path, InvocationStatus]:
    """Execute the current invocation and write its Result File.

    :param scenario: Parsed scenario model for this invocation.
    :param controller_bind_address: Bind address for the Controller Receipt Channel.
    :param results_dir: Directory where result files are written.
    :returns: Written result file path and its invocation status.
    """

    controller_endpoint = parse_reachable_service_endpoint(controller_bind_address)
    normalized_controller_bind_address = (
        f"{controller_endpoint.host}:{controller_endpoint.port}"
    )
    update_server_bind_address = derive_update_server_bind_address(
        normalized_controller_bind_address
    )
    resolved_invocation_runner = (
        create_default_invocation_runner() if invocation_runner is None else invocation_runner
    )
    invocation_instance = allocate_invocation_instance(results_dir)
    started_at = _utc_now()

    async with start_update_server(bind_address=update_server_bind_address) as update_server:
        try:
            expected_bundle = await fetch_expected_bundle_from_update_server(
                update_server_base_url=update_server.base_url,
                bundle_id=DEFAULT_BUNDLE.bundle_id,
            )
        except RuntimeError as invocation_error:
            return (
                _write_failed_invocation_result(
                    results_dir=results_dir,
                    invocation_instance=invocation_instance,
                    scenario_file=scenario.scenario_file,
                    started_at=started_at,
                    invocation_error={
                        "stage": "manifest_fetch",
                        "detail": str(invocation_error),
                    },
                ),
                "failed",
            )

        expected_client_ids = build_expected_client_ids(scenario.client_count)

        async with start_controller_server(
            bind_address=normalized_controller_bind_address,
            invocation_instance=invocation_instance,
            expected_client_ids=expected_client_ids,
            expected_bundle=expected_bundle,
        ) as controller_server:
            try:
                invocation_session = resolved_invocation_runner.start_session(
                    invocation_instance=invocation_instance,
                    client_ids=expected_client_ids,
                    controller_reportback_url=service_url(normalized_controller_bind_address),
                    update_server_url=update_server.base_url,
                    bundle_id=expected_bundle.bundle_id,
                )
            except RuntimeSetupError as invocation_error:
                return (
                    _write_failed_invocation_result(
                        results_dir=results_dir,
                        invocation_instance=invocation_instance,
                        scenario_file=scenario.scenario_file,
                        started_at=started_at,
                        expected_bundle=expected_bundle.to_payload(),
                        invocation_error={
                            "stage": "runtime_setup",
                            "detail": str(invocation_error),
                        },
                    ),
                    "failed",
                )

            if invocation_session.startup_failures:
                await invocation_session.stop_remaining_subjects()
            else:
                all_subjects_done_task = asyncio.create_task(
                    invocation_session.wait_for_subjects()
                )
                all_client_outcomes_recorded_task = asyncio.create_task(
                    controller_server.wait_for_client_outcomes(
                        scenario.receipt_timeout_seconds
                    )
                )
                await _wait_for_invocation_completion(
                    invocation_session=invocation_session,
                    all_subjects_done_task=all_subjects_done_task,
                    all_client_outcomes_recorded_task=all_client_outcomes_recorded_task,
                    expected_client_ids=expected_client_ids,
                    controller_server=controller_server,
                )
    finished_at = _utc_now()
    invocation_dir = _ensure_invocation_dir(results_dir, invocation_instance)
    client_outcomes = [
        _result_client_outcome(
            client_id=client_id,
            controller_client_outcome=controller_server.client_outcomes.get(client_id),
            expected_bundle=expected_bundle,
            startup_failures=invocation_session.startup_failures,
        )
        for client_id in expected_client_ids
    ]
    finalization_error = await invocation_session.finalize(
        invocation_dir=invocation_dir,
        failed_client_ids=tuple(
            outcome.client_id
            for outcome in client_outcomes
            if outcome.client_status != ClientStatus.SUCCESS
        ),
        keep_containers=keep_containers,
    )
    invocation_status = _derive_invocation_status(client_outcomes)
    payload_invocation_error: InvocationErrorPayload | None = None
    if finalization_error is not None:
        invocation_status = "failed"
        payload_invocation_error = {
            "stage": "runtime_finalization",
            "detail": finalization_error,
        }
    result_file = _write_result_file(
        results_dir=results_dir,
        payload=_result_file_payload(
            invocation_instance=invocation_instance,
            scenario_file=scenario.scenario_file,
            invocation_status=invocation_status,
            started_at=started_at,
            finished_at=finished_at,
            expected_bundle=expected_bundle.to_payload(),
            invocation_error=payload_invocation_error,
            client_outcomes=client_outcomes,
        ),
    )
    return result_file, invocation_status


async def _wait_for_invocation_completion(
    *,
    invocation_session: InvocationSession,
    all_subjects_done_task: asyncio.Task[None],
    all_client_outcomes_recorded_task: asyncio.Task[bool],
    expected_client_ids: tuple[str, ...],
    controller_server: ControllerServer,
) -> None:
    waitables: set[asyncio.Task[object]] = {
        cast(asyncio.Task[object], all_subjects_done_task),
        cast(asyncio.Task[object], all_client_outcomes_recorded_task),
    }
    done, _pending = await asyncio.wait(
        waitables,
        return_when=asyncio.FIRST_COMPLETED,
    )

    if all_client_outcomes_recorded_task in done:
        all_client_outcomes_recorded = all_client_outcomes_recorded_task.result()
        if not all_client_outcomes_recorded:
            await invocation_session.stop_remaining_subjects()
            await all_subjects_done_task
            return

        await all_subjects_done_task
        return

    await all_subjects_done_task
    if _have_outcomes_for_every_expected_client(
        expected_client_ids=expected_client_ids,
        controller_client_outcomes=controller_server.client_outcomes,
    ):
        all_client_outcomes_recorded_task.cancel()
        await asyncio.gather(all_client_outcomes_recorded_task, return_exceptions=True)
        return

    await all_client_outcomes_recorded_task


def _have_outcomes_for_every_expected_client(
    *,
    expected_client_ids: tuple[str, ...],
    controller_client_outcomes: dict[str, ClientOutcome],
) -> bool:
    return all(client_id in controller_client_outcomes for client_id in expected_client_ids)


def _result_client_outcome(
    *,
    client_id: str,
    controller_client_outcome: ClientOutcome | None,
    expected_bundle: Bundle,
    startup_failures: Mapping[str, str],
) -> ClientOutcome:
    startup_failure = startup_failures.get(client_id)
    if startup_failure is not None:
        return ClientOutcome(
            client_id=client_id,
            client_status=ClientStatus.STARTUP_FAILED,
            bundle_id=expected_bundle.bundle_id,
            error_detail=startup_failure,
        )

    if controller_client_outcome is None:
        return ClientOutcome(
            client_id=client_id,
            client_status=ClientStatus.TIMED_OUT,
            bundle_id=expected_bundle.bundle_id,
            error_detail=TIMED_OUT_ERROR_DETAIL,
        )

    return ClientOutcome(
        client_id=client_id,
        client_status=ClientStatus(controller_client_outcome.client_status),
        bundle_id=expected_bundle.bundle_id,
        error_detail=controller_client_outcome.error_detail,
        reported_bundle=controller_client_outcome.reported_bundle,
    )


def _result_file_payload(
    *,
    invocation_instance: int,
    scenario_file: Path,
    invocation_status: InvocationStatus,
    started_at: str,
    finished_at: str,
    expected_bundle: BundlePayload | None,
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
        "clients": [outcome.to_payload() for outcome in client_outcomes],
    }


def _write_failed_invocation_result(
    *,
    results_dir: Path,
    invocation_instance: int,
    scenario_file: Path,
    started_at: str,
    invocation_error: InvocationErrorPayload,
    expected_bundle: BundlePayload | None = None,
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
    invocation_dir = _ensure_invocation_dir(results_dir, payload["invocation_instance"])
    result_file = invocation_dir / "result.json"
    result_file.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
    return result_file


def _ensure_invocation_dir(results_dir: Path, invocation_instance: int) -> Path:
    invocation_dir = results_dir / invocation_directory_name(invocation_instance)
    invocation_dir.mkdir(parents=True, exist_ok=True)
    return invocation_dir


def allocate_invocation_instance(results_dir: Path) -> int:
    """Allocate the next invocation_instance from existing result filenames.

    :param results_dir: Directory containing prior result files.
    :returns: Next invocation number, starting at 1.
    """

    highest_invocation_instance = 0

    if not results_dir.exists():
        return 1

    for result_file in results_dir.iterdir():
        if result_file.is_dir():
            match = INVOCATION_DIRECTORY_NAME_PATTERN.fullmatch(result_file.name)
            if match is None or not (result_file / "result.json").is_file():
                continue
            highest_invocation_instance = max(
                highest_invocation_instance,
                int(match.group(1)),
            )
            continue

        match = RESULT_FILE_NAME_PATTERN.fullmatch(result_file.name)
        if match is not None:
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


async def fetch_expected_bundle_from_update_server(
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
        async with httpx.AsyncClient() as client:
            response = await client.get(manifest_url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as fetch_error:
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


def _utc_now() -> str:
    """Return the current UTC timestamp as an ISO 8601 string.

    :returns: UTC timestamp with a trailing Z suffix.
    """

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
