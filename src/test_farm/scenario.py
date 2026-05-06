"""Scenario file parsing for baseline invocations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]


class ScenarioFileError(ValueError):
    """Raised when a scenario file does not match the supported M1 shape."""


@dataclass(frozen=True)
class Scenario:
    """The supported M1 scenario contract."""

    client_count: int


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
            f"Scenario file {path} must contain a mapping with only client_count."
        )

    return Scenario(client_count=_parse_client_count(path, raw_data))


def _parse_client_count(path: Path, raw_data: dict[str, Any]) -> int:
    """Validate the only supported scenario field.

    :param path: Path to the scenario YAML file.
    :param raw_data: Parsed raw YAML mapping.
    :returns: Validated client count.
    :raises ScenarioFileError: If the mapping shape is invalid.
    """

    expected_fields = {"client_count"}
    actual_fields = set(raw_data)
    missing_fields = expected_fields - actual_fields
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
