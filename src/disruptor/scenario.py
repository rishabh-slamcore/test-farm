"""Disruptor Scenario File parsing."""

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import yaml  # type: ignore[import-untyped]

from disruptor.models import DiscoveredDevice, NetworkImpairment


class DisruptorScenarioFileError(ValueError):
    """Raised when a Disruptor Scenario File is malformed."""


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
class DisruptorScenario:
    """The Disruptor Scenario File contract."""

    scenario_file: Path
    default_impairment: NetworkImpairment | None
    overrides: tuple["DisruptorPolicyOverride", ...] = ()


class Selector(Protocol):
    """A device selector in a Disruptor policy override."""

    def accept(self, device: DiscoveredDevice) -> bool:
        """Return true if device_name matches selector."""
        ...


@dataclass(frozen=True)
class DeviceNameMatch:
    """Match devices by exact discovered device name."""

    device_names: set[str]

    def accept(self, device: DiscoveredDevice) -> bool:
        return device.device_id in self.device_names


@dataclass(frozen=True)
class RegexMatch:
    """Match devices with a regular expression against the discovered device name."""

    pattern: str

    def accept(self, device: DiscoveredDevice) -> bool:
        return re.search(self.pattern, device.device_id) is not None


@dataclass(frozen=True)
class VariantMatch:
    """Match devices by exact discovered device variant."""

    variant: str

    def accept(self, device: DiscoveredDevice) -> bool:
        return self.variant == device.variant


@dataclass(frozen=True)
class DisruptorPolicyOverride:
    """A named ordered Disruptor policy override."""

    name: str
    selector: Selector
    impairment: NetworkImpairment | None


def load_disruptor_scenario_file(path: Path) -> DisruptorScenario:
    """Load and validate a Disruptor Scenario File.

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
            f"Disruptor Scenario must contain a network_impairment mapping."
        )

    _validate_scenario_fields(
        actual_fields=set(raw_data),
        expected_fields={"network_impairment"},
    )

    raw_network_impairment = raw_data["network_impairment"]
    if not isinstance(raw_network_impairment, dict):
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set network_impairment to a mapping."
        )

    _validate_scenario_fields(
        actual_fields={f"network_impairment.{field}" for field in raw_network_impairment},
        expected_fields={"network_impairment.default", "network_impairment.overrides"},
        optional_fields={"network_impairment.overrides"},
    )

    default_impairment = _parse_disruptor_impairment_policy(
        raw_network_impairment["default"],
        field_path="network_impairment.default",
    )

    return DisruptorScenario(
        scenario_file=path,
        default_impairment=default_impairment,
        overrides=_parse_disruptor_policy_overrides(
            raw_network_impairment.get("overrides", []),
        ),
    )


def _validate_scenario_fields(
    *,
    actual_fields: set[str],
    expected_fields: set[str],
    optional_fields: set[str] | None = None,
) -> None:
    optional_fields = optional_fields or set()
    required_fields = expected_fields - optional_fields
    if required_fields <= actual_fields <= expected_fields:
        return

    unknown_fields = actual_fields - expected_fields
    if unknown_fields:
        unknown_field_list = ", ".join(sorted(unknown_fields))
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario File contains unknown fields: {unknown_field_list}."
        )

    missing_fields = required_fields - actual_fields
    missing_field_list = ", ".join(sorted(missing_fields))
    raise DisruptorScenarioFileError(
        f"Disruptor Scenario File is missing required field {missing_field_list}."
    )


def _parse_disruptor_policy_overrides(
    raw_overrides: Any,
) -> tuple[DisruptorPolicyOverride, ...]:
    if not isinstance(raw_overrides, list):
        raise DisruptorScenarioFileError(
            "Disruptor Scenario must set network_impairment.overrides to a sequence."
        )

    return tuple(
        _parse_disruptor_policy_override(raw_override, override_index)
        for override_index, raw_override in enumerate(raw_overrides)
    )


def _parse_disruptor_policy_override(
    raw_override: Any,
    override_index: int,
) -> DisruptorPolicyOverride:
    field_path = f"network_impairment.overrides[{override_index}]"
    if not isinstance(raw_override, dict):
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set {field_path} to a mapping."
        )

    _validate_scenario_fields(
        actual_fields={f"{field_path}.{field}" for field in raw_override},
        expected_fields={
            f"{field_path}.name",
            f"{field_path}.device_match",
            f"{field_path}.regex_match",
            f"{field_path}.variant_match",
            f"{field_path}.impairment",
        },
        optional_fields={
            f"{field_path}.name",
            f"{field_path}.device_match",
            f"{field_path}.regex_match",
            f"{field_path}.variant_match",
        },
    )

    raw_name = raw_override.get("name", f"override-{override_index}")
    if not isinstance(raw_name, str) or raw_name == "":
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set {field_path}.name to a non-empty string."
        )

    return DisruptorPolicyOverride(
        name=raw_name,
        selector=_parse_disruptor_selector(raw_override, field_path),
        impairment=_parse_disruptor_impairment_policy(
            raw_override["impairment"],
            field_path=f"{field_path}.impairment",
        ),
    )


def _parse_disruptor_selector(
    raw_override: dict[str, Any],
    field_path: str,
) -> Selector:
    accepted_selector_types = ("device_match", "regex_match", "variant_match")
    selector_type_count = sum(
        selector_type in raw_override for selector_type in accepted_selector_types
    )
    if selector_type_count != 1:
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set exactly one of "
            f"{field_path}.device_match, {field_path}.regex_match, or {field_path}.variant_match"
        )

    if "device_match" in raw_override:
        return _parse_device_selector(
            raw_override["device_match"],
            f"{field_path}.device_match",
        )

    if "regex_match" in raw_override:
        return _parse_regex_selector(
            raw_override["regex_match"],
            f"{field_path}.regex_match",
        )

    return _parse_variant_selector(
        raw_override["variant_match"], f"{field_path}.variant_match"
    )


def _parse_device_selector(
    raw_selectors: Any,
    field_path: str,
) -> DeviceNameMatch:
    if not isinstance(raw_selectors, list) or raw_selectors == []:
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set {field_path} to a non-empty sequence."
        )

    selectors: list[str] = []
    for selector_index, raw_selector in enumerate(raw_selectors):
        if not isinstance(raw_selector, str) or raw_selector == "":
            raise DisruptorScenarioFileError(
                "Disruptor Scenario must set "
                f"{field_path}[{selector_index}] to a non-empty string."
            )
        selectors.append(raw_selector)

    return DeviceNameMatch(set(selectors))


def _parse_regex_selector(
    raw_selector: Any,
    field_path: str,
) -> RegexMatch:
    if not isinstance(raw_selector, str) or raw_selector == "":
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set {field_path} to a non-empty string."
        )

    try:
        re.compile(raw_selector)
    except re.error as error:
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set {field_path} to a valid regular expression."
        ) from error

    return RegexMatch(raw_selector)


def _parse_variant_selector(
    raw_selector: Any,
    field_path: str,
) -> VariantMatch:
    if not isinstance(raw_selector, str) or raw_selector == "":
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set {field_path} to a non-empty string."
        )

    return VariantMatch(variant=raw_selector)


def _parse_disruptor_impairment_policy(
    raw_impairment_policy: Any,
    *,
    field_path: str,
) -> NetworkImpairment | None:
    if raw_impairment_policy == "none":
        return None

    try:
        impairment = _parse_network_impairment(
            {"network_impairment": raw_impairment_policy},
        )
    except DisruptorScenarioFileError as error:
        message = str(error)
        message = message.replace("network_impairment", field_path)
        raise DisruptorScenarioFileError(message) from error

    if impairment is None:
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set {field_path} to a mapping or none."
        )

    return impairment


def _parse_network_impairment(
    raw_data: dict[str, Any],
) -> NetworkImpairment | None:
    raw_network_impairment = raw_data.get("network_impairment")
    if raw_network_impairment is None:
        return None

    if not isinstance(raw_network_impairment, dict):
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set network_impairment to a mapping."
        )

    actual_fields = set(raw_network_impairment)
    expected_fields = {"delay", "loss", "bandwidth_limit"}
    unknown_fields = actual_fields - expected_fields
    if unknown_fields:
        unknown_field_list = ", ".join(sorted(unknown_fields))
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario contains unknown network_impairment fields: {unknown_field_list}."
        )

    if actual_fields == set():
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set at least one network_impairment field."
        )

    return NetworkImpairment(
        delay=_parse_network_impairment_delay(raw_network_impairment),
        loss=_parse_network_impairment_loss(raw_network_impairment),
        bandwidth_limit=_parse_network_impairment_bandwidth_limit(raw_network_impairment),
    )


def _parse_network_impairment_delay(
    raw_network_impairment: dict[str, Any],
) -> float | None:
    raw_delay = raw_network_impairment.get("delay")
    if raw_delay is None:
        return None

    if not isinstance(raw_delay, str) or not _DELAY_PATTERN.fullmatch(raw_delay):
        raise DisruptorScenarioFileError(
            f"Disruptor Scenario must set network_impairment.delay to a duration like 100ms."
        )

    return _parse_delay_seconds(raw_delay)


def _parse_network_impairment_loss(
    raw_network_impairment: dict[str, Any],
) -> float | None:
    raw_loss = raw_network_impairment.get("loss")
    if raw_loss is None:
        return None

    parsed_loss: float
    if isinstance(raw_loss, bool):
        raise DisruptorScenarioFileError(
            "Disruptor Scenario must set network_impairment.loss to a percentage between 0 and 100."
        )

    if isinstance(raw_loss, int | float):
        parsed_loss = float(raw_loss)
    elif isinstance(raw_loss, str) and _LOSS_PERCENT_PATTERN.fullmatch(raw_loss):
        parsed_loss = float(raw_loss[:-1])
    else:
        raise DisruptorScenarioFileError(
            "Disruptor Scenario must set network_impairment.loss to a percentage between 0 and 100."
        )

    if parsed_loss < 0 or parsed_loss > 100:
        raise DisruptorScenarioFileError(
            "Disruptor Scenario must set network_impairment.loss to a percentage between 0 and 100."
        )

    return parsed_loss


def _parse_network_impairment_bandwidth_limit(
    raw_network_impairment: dict[str, Any],
) -> int | None:
    raw_bandwidth_limit = raw_network_impairment.get("bandwidth_limit")
    if raw_bandwidth_limit is None:
        return None

    if not isinstance(raw_bandwidth_limit, str) or not _BANDWIDTH_LIMIT_PATTERN.fullmatch(
        raw_bandwidth_limit
    ):
        raise DisruptorScenarioFileError(
            "Disruptor Scenario must set network_impairment.bandwidth_limit to a rate like 1mbit."
        )

    parsed_bandwidth_limit = _parse_bandwidth_limit_bps(raw_bandwidth_limit)
    if parsed_bandwidth_limit <= 0:
        raise DisruptorScenarioFileError(
            "Disruptor Scenario must set network_impairment.bandwidth_limit to a rate like 1mbit."
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


def _parse_bandwidth_limit_bps(raw_bandwidth_limit: str) -> int:
    matched_unit = next(
        unit for unit in _BANDWIDTH_UNITS_BY_LENGTH if raw_bandwidth_limit.endswith(unit)
    )
    parsed_value = Decimal(raw_bandwidth_limit[: -len(matched_unit)])
    parsed_bandwidth_limit = parsed_value * _BANDWIDTH_FACTORS_BY_UNIT[matched_unit]
    if parsed_bandwidth_limit != parsed_bandwidth_limit.to_integral_value():
        raise DisruptorScenarioFileError(
            "Disruptor Scenario must set network_impairment.bandwidth_limit to a rate like 1mbit."
        )

    return int(parsed_bandwidth_limit)
