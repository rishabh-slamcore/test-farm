"""Timed-out baseline invocation execution."""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal, TypedDict

RESULT_FILE_NAME_PATTERN = re.compile(r"result_(\d+)\.json$")
DEFAULT_BUNDLE_ID = "baseline"
TIMED_OUT_ERROR_DETAIL = "No verified receipt received before timeout."


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


class ExpectedBundlePayload(TypedDict):
    """Serialized expected bundle metadata."""

    bundle_id: str
    byte_count: int | None
    checksum: str | None


class ClientOutcomePayload(TypedDict):
    """Serialized one-client result entry."""

    client_id: str
    client_status: ClientStatus
    bundle_id: str
    error_detail: str | None


class ResultFilePayload(TypedDict):
    """Serialized top-level result file payload."""

    invocation_instance: int
    scenario_file: str
    invocation_status: InvocationStatus
    started_at: str
    finished_at: str
    expected_bundle: ExpectedBundlePayload
    clients: list[ClientOutcomePayload]


def execute_timed_out_invocation(
    scenario_file: Path,
    client_count: int,
    receipt_timeout_seconds: int,
    results_dir: Path,
) -> Path:
    """Write the deterministic timed-out result for the current invocation.

    :param scenario_file: Scenario file path supplied to the CLI.
    :param client_count: Number of clients requested by the scenario file.
    :param receipt_timeout_seconds: Receipt wait duration before timing out.
    :param results_dir: Directory where result files are written.
    :returns: Path to the written result file.
    """

    invocation_instance = allocate_invocation_instance(results_dir)
    started_at = _utc_now()
    if receipt_timeout_seconds > 0:
        time.sleep(receipt_timeout_seconds)
    finished_at = _utc_now()

    client_outcomes = [
        ClientOutcome(
            client_id=_client_id(index),
            client_status=ClientStatus.TIMED_OUT,
            bundle_id=DEFAULT_BUNDLE_ID,
            error_detail=TIMED_OUT_ERROR_DETAIL,
        )
        for index in range(1, client_count + 1)
    ]

    payload: ResultFilePayload = {
        "invocation_instance": invocation_instance,
        "scenario_file": str(scenario_file),
        "invocation_status": _derive_invocation_status(client_outcomes),
        "started_at": started_at,
        "finished_at": finished_at,
        "expected_bundle": {
            "bundle_id": DEFAULT_BUNDLE_ID,
            "byte_count": None,
            "checksum": None,
        },
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

    results_dir.mkdir(parents=True, exist_ok=True)
    result_file = results_dir / f"result_{invocation_instance}.json"
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
