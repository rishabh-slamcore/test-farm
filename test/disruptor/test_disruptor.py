"""Disruptor scenario and planning tests."""

from collections.abc import Callable
from pathlib import Path

import pytest

from test_farm.disruptor import DiscoveredDevice, build_default_disruptor_tc_plan
from test_farm.scenario import (
    DisruptorScenarioFileError,
    ScenarioFileError,
    load_disruptor_scenario_file,
)


def test_disruptor_scenario_file_error_is_a_scenario_file_error() -> None:
    assert issubclass(DisruptorScenarioFileError, ScenarioFileError)


def test_load_disruptor_scenario_file_parses_default_impairment(tmp_path: Path) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        (
            "network_impairment:\n"
            "  default:\n"
            "    delay: 250ms\n"
            "    loss: 12.5%\n"
            "    bandwidth_limit: 1.5kbit\n"
        ),
        encoding="utf-8",
    )

    scenario = load_disruptor_scenario_file(scenario_file)

    assert scenario.scenario_file == scenario_file
    assert scenario.default_impairment.delay == 0.25
    assert scenario.default_impairment.loss == 12.5
    assert scenario.default_impairment.bandwidth_limit == 1_500


def test_load_disruptor_scenario_file_rejects_invalid_default_impairment(
    tmp_path: Path,
) -> None:
    scenario_file = tmp_path / "invalid.yaml"
    scenario_file.write_text(
        ("network_impairment:\n" "  default:\n" "    delay: 100\n"),
        encoding="utf-8",
    )

    with pytest.raises(
        DisruptorScenarioFileError,
        match="must set network_impairment.default.delay to a duration like 100ms",
    ):
        load_disruptor_scenario_file(scenario_file)


def test_default_disruptor_scenario_builds_typed_plan_for_discovered_devices(
    tmp_path: Path,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        ("network_impairment:\n" "  default:\n" "    delay: 100ms\n" "    loss: 5\n"),
        encoding="utf-8",
    )
    scenario = load_disruptor_scenario_file(scenario_file)

    plan = build_default_disruptor_tc_plan(
        interface_name="wlan0",
        devices=tuple(discovered_devices(2)),
        default_impairment=scenario.default_impairment,
    )

    assert plan.interface_name == "wlan0"
    assert [device_plan.policy_name for device_plan in plan.device_plans] == [
        "default",
        "default",
    ]
    assert [device_plan.impairment for device_plan in plan.device_plans] == [
        scenario.default_impairment,
        scenario.default_impairment,
    ]
    assert [device_plan.class_id for device_plan in plan.device_plans] == ["1:10", "1:20"]
    assert (
        "tc filter add dev wlan0 parent 1: protocol ip prio 2 u32 match ip dst "
        "192.0.2.11/32 flowid 1:20"
    ) in plan.commands
