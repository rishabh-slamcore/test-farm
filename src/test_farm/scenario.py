"""Scenario file parsing for baseline and impaired invocations."""

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from test_farm.network_impairment import NetworkImpairment


class ScenarioFileError(ValueError):
    """Raised when a scenario file does not match the supported baseline shape."""


_DELAY_PATTERN = re.compile(r"^\d+(?:\.\d+)?(?:us|ms|s)$")
_BANDWIDTH_LIMIT_PATTERN = re.compile(r"^\d+(?:\.\d+)?(?:bit|kbit|mbit|gbit|tbit)$")
_LOSS_PERCENT_PATTERN = re.compile(r"^\d+(?:\.\d+)?%$")
_DELAY_FACTORS_BY_UNIT = {
    "us": Decimal("0.000001"),
    "ms": Decimal("0.001"),
    "s": Decimal("1"),
}
_BANDWIDTH_FACTORS_BY_UNIT = {
    "bit": Decimal("1"),
    "kbit": Decimal("1000"),
    "mbit": Decimal("1000000"),
    "gbit": Decimal("1000000000"),
    "tbit": Decimal("1000000000000"),
}
_BANDWIDTH_UNITS_BY_LENGTH = tuple(sorted(_BANDWIDTH_FACTORS_BY_UNIT, key=len, reverse=True))


@dataclass(frozen=True)
class Scenario:
    """The supported Scenario File contract."""

    scenario_file: Path
    client_count: int
    receipt_timeout_seconds: float
    network_impairment: NetworkImpairment | None = None


class DisruptorScenarioFileError(ValueError):
    """Raised when a Disruptor Scenario File is malformed."""


@dataclass(frozen=True)
class DisruptorScenario:
    """The default-only Disruptor Scenario File contract."""

    scenario_file: Path
    default_impairment: NetworkImpairment


def load_scenario_file(path: Path) -> Scenario:
    """Load and validate a scenario file.

    :param path: Path to the scenario YAML file.
    :returns: Parsed scenario model.
    :raises ScenarioFileError: If the YAML is malformed or uses unsupported fields.
    """

    try:
        raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ScenarioFileError(f"Could not read scenario file {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ScenarioFileError(f"Scenario file {path} is not valid YAML: {error}") from error

    if not isinstance(raw_data, dict):
        raise ScenarioFileError(
            "Scenario file "
            f"{path} must contain a mapping with only client_count, "
            "receipt_timeout_seconds, and optional network_impairment."
        )

    return Scenario(
        scenario_file=path,
        client_count=_parse_client_count(path, raw_data),
        receipt_timeout_seconds=_parse_receipt_timeout_seconds(path, raw_data),
        network_impairment=_parse_network_impairment(path, raw_data),
    )


def load_disruptor_scenario_file(path: Path) -> DisruptorScenario:
    """Load and validate a default-only Disruptor Scenario File.

    :param path: Path to the scenario YAML file.
    :returns: Parsed Disruptor scenario model.
    :raises DisruptorScenarioFileError: If the YAML or scenario shape is invalid.
    """

    try:
        raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise DisruptorScenarioFileError(
            f"Could not read Disruptor Scenario File {path}: {error}"
        ) from error
    except yaml.YAMLError as error:
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario File {path} is not valid YAML: {error}"
        ) from error

    if not isinstance(raw_data, dict):
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario File {path} must contain a network_impairment mapping."
        )

    _validate_scenario_fields(
        actual_fields=set(raw_data),
        expected_fields={"network_impairment"},
    )

    raw_network_impairment = raw_data["network_impairment"]
    if not isinstance(raw_network_impairment, dict):
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario File {path} must set network_impairment to a mapping."
        )

    _validate_scenario_fields(
        actual_fields={f"network_impairment.{field}" for field in raw_network_impairment},
        expected_fields={"network_impairment.default"},
    )

    try:
        default_impairment = _parse_network_impairment(
            path,
            {"network_impairment": raw_network_impairment["default"]},
        )
    except ScenarioFileError as error:
        message = str(error)
        message = message.replace("Scenario file", "Disruptor Scenario File")
        message = message.replace("network_impairment", "network_impairment.default")
        raise DisruptorScenarioFileError(message) from error

    if default_impairment is None:
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario File {path} must set network_impairment.default to a mapping."
        )

    return DisruptorScenario(
        scenario_file=path,
        default_impairment=default_impairment,
    )


def _validate_scenario_fields(
    *,
    actual_fields: set[str],
    expected_fields: set[str],
) -> None:
    if actual_fields == expected_fields:
        return

    unknown_fields = actual_fields - expected_fields
    if unknown_fields:
        unknown_field_list = ", ".join(sorted(unknown_fields))
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario File contains unknown fields: {unknown_field_list}."
        )

    missing_fields = expected_fields - actual_fields
    missing_field_list = ", ".join(sorted(missing_fields))
    raise DisruptorScenarioFileError(
        f"Disruptor Scenario File is missing required field {missing_field_list}."
    )


def _parse_client_count(path: Path, raw_data: dict[str, Any]) -> int:
    """Validate the supported client-count field.

    :param path: Path to the scenario YAML file.
    :param raw_data: Parsed raw YAML mapping.
    :returns: Validated client count.
    :raises ScenarioFileError: If the mapping shape is invalid.
    """

    required_fields = {"client_count", "receipt_timeout_seconds"}
    expected_fields = required_fields | {"network_impairment"}
    actual_fields = set(raw_data)
    missing_fields = required_fields - actual_fields
    unknown_fields = actual_fields - expected_fields

    if missing_fields:
        missing_field = sorted(missing_fields)[0]
        raise ScenarioFileError(
            f"Scenario file {path} is missing required field {missing_field}."
        )

    if unknown_fields:
        unknown_field_list = ", ".join(sorted(unknown_fields))
        raise ScenarioFileError(
            f"Scenario file {path} contains unknown fields: {unknown_field_list}."
        )

    raw_client_count = raw_data["client_count"]
    if (
        isinstance(raw_client_count, bool)
        or not isinstance(raw_client_count, int)
        or raw_client_count < 1
    ):
        raise ScenarioFileError(
            f"Scenario file {path} must set client_count to a positive integer."
        )

    return cast(int, raw_client_count)


def _parse_receipt_timeout_seconds(path: Path, raw_data: dict[str, Any]) -> float:
    """Validate the supported receipt-timeout field.

    :param path: Path to the scenario YAML file.
    :param raw_data: Parsed raw YAML mapping.
    :returns: Validated receipt timeout in seconds.
    :raises ScenarioFileError: If the mapping shape is invalid.
    """

    raw_receipt_timeout_seconds = raw_data["receipt_timeout_seconds"]
    if (
        isinstance(raw_receipt_timeout_seconds, bool)
        or not isinstance(raw_receipt_timeout_seconds, int | float)
        or raw_receipt_timeout_seconds < 0
    ):
        raise ScenarioFileError(
            f"Scenario file {path} must set receipt_timeout_seconds to a non-negative number."
        )

    return float(raw_receipt_timeout_seconds)


def _parse_network_impairment(
    path: Path,
    raw_data: dict[str, Any],
) -> NetworkImpairment | None:
    raw_network_impairment = raw_data.get("network_impairment")
    if raw_network_impairment is None:
        return None

    if not isinstance(raw_network_impairment, dict):
        raise ScenarioFileError(
            f"Scenario file {path} must set network_impairment to a mapping."
        )

    actual_fields = set(raw_network_impairment)
    expected_fields = {"delay", "loss", "bandwidth_limit"}
    unknown_fields = actual_fields - expected_fields
    if unknown_fields:
        unknown_field_list = ", ".join(sorted(unknown_fields))
        raise ScenarioFileError(
            "Scenario file "
            f"{path} contains unknown network_impairment fields: {unknown_field_list}."
        )

    if actual_fields == set():
        raise ScenarioFileError(
            f"Scenario file {path} must set at least one network_impairment field."
        )

    return NetworkImpairment(
        delay=_parse_network_impairment_delay(path, raw_network_impairment),
        loss=_parse_network_impairment_loss(path, raw_network_impairment),
        bandwidth_limit=_parse_network_impairment_bandwidth_limit(
            path, raw_network_impairment
        ),
    )


def _parse_network_impairment_delay(
    path: Path,
    raw_network_impairment: dict[str, Any],
) -> float | None:
    raw_delay = raw_network_impairment.get("delay")
    if raw_delay is None:
        return None

    if not isinstance(raw_delay, str) or not _DELAY_PATTERN.fullmatch(raw_delay):
        raise ScenarioFileError(
            f"Scenario file {path} must set network_impairment.delay to a duration like 100ms."
        )

    return _parse_delay_seconds(raw_delay)


def _parse_network_impairment_loss(
    path: Path,
    raw_network_impairment: dict[str, Any],
) -> float | None:
    raw_loss = raw_network_impairment.get("loss")
    if raw_loss is None:
        return None

    parsed_loss: float
    if isinstance(raw_loss, bool):
        raise ScenarioFileError(
            f"Scenario file {path} must set network_impairment.loss to a percentage between 0 and 100."
        )

    if isinstance(raw_loss, int | float):
        parsed_loss = float(raw_loss)
    elif isinstance(raw_loss, str) and _LOSS_PERCENT_PATTERN.fullmatch(raw_loss):
        parsed_loss = float(raw_loss[:-1])
    else:
        raise ScenarioFileError(
            f"Scenario file {path} must set network_impairment.loss to a percentage between 0 and 100."
        )

    if parsed_loss < 0 or parsed_loss > 100:
        raise ScenarioFileError(
            f"Scenario file {path} must set network_impairment.loss to a percentage between 0 and 100."
        )

    return parsed_loss


def _parse_network_impairment_bandwidth_limit(
    path: Path,
    raw_network_impairment: dict[str, Any],
) -> int | None:
    raw_bandwidth_limit = raw_network_impairment.get("bandwidth_limit")
    if raw_bandwidth_limit is None:
        return None

    if not isinstance(raw_bandwidth_limit, str) or not _BANDWIDTH_LIMIT_PATTERN.fullmatch(
        raw_bandwidth_limit
    ):
        raise ScenarioFileError(
            "Scenario file "
            f"{path} must set network_impairment.bandwidth_limit to a rate like 1mbit."
        )

    parsed_bandwidth_limit = _parse_bandwidth_limit_bps(path, raw_bandwidth_limit)
    if parsed_bandwidth_limit <= 0:
        raise ScenarioFileError(
            "Scenario file "
            f"{path} must set network_impairment.bandwidth_limit to a rate like 1mbit."
        )

    return parsed_bandwidth_limit


def _parse_delay_seconds(raw_delay: str) -> float:
    if raw_delay.endswith("us"):
        delay_unit = "us"
    elif raw_delay.endswith("ms"):
        delay_unit = "ms"
    else:
        delay_unit = "s"

    delay_value = Decimal(raw_delay[: -len(delay_unit)])
    return float(delay_value * _DELAY_FACTORS_BY_UNIT[delay_unit])


def _parse_bandwidth_limit_bps(path: Path, raw_bandwidth_limit: str) -> int:
    matched_unit = next(
        unit for unit in _BANDWIDTH_UNITS_BY_LENGTH if raw_bandwidth_limit.endswith(unit)
    )
    parsed_value = Decimal(raw_bandwidth_limit[: -len(matched_unit)])
    parsed_bandwidth_limit = parsed_value * _BANDWIDTH_FACTORS_BY_UNIT[matched_unit]
    if parsed_bandwidth_limit != parsed_bandwidth_limit.to_integral_value():
        raise ScenarioFileError(
            "Scenario file "
            f"{path} must set network_impairment.bandwidth_limit to a rate like 1mbit."
        )

    return int(parsed_bandwidth_limit)
