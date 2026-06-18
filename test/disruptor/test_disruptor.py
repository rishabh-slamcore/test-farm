"""Disruptor scenario and planning tests."""

from collections.abc import Callable
from pathlib import Path

import pytest

from test_farm.disruptor import DiscoveredDevice, build_disruptor_tc_plan
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
    assert scenario.default_impairment is not None
    assert scenario.default_impairment.delay == 0.25
    assert scenario.default_impairment.loss == 12.5
    assert scenario.default_impairment.bandwidth_limit == 1_500


def test_load_disruptor_scenario_file_parses_ordered_overrides(tmp_path: Path) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        (
            "network_impairment:\n"
            "  default:\n"
            "    delay: 100ms\n"
            "  overrides:\n"
            "    - name: high-loss\n"
            "      selectors:\n"
            "        - sc-aware-11\n"
            "      impairment:\n"
            "        loss: 25%\n"
            "    - name: control\n"
            "      selectors:\n"
            "        - sc-aware-12\n"
            "      impairment: none\n"
        ),
        encoding="utf-8",
    )

    scenario = load_disruptor_scenario_file(scenario_file)

    assert [override.name for override in scenario.overrides] == ["high-loss", "control"]
    assert scenario.overrides[0].selectors == ("sc-aware-11",)
    assert scenario.overrides[0].impairment is not None
    assert scenario.overrides[0].impairment.loss == 25.0
    assert scenario.overrides[1].selectors == ("sc-aware-12",)
    assert scenario.overrides[1].impairment is None


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

    plan = build_disruptor_tc_plan(
        interface_name="wlan0",
        devices=tuple(discovered_devices(2)),
        scenario=scenario,
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


def test_disruptor_tc_plan_uses_first_matching_override(
    tmp_path: Path,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        (
            "network_impairment:\n"
            "  default:\n"
            "    delay: 100ms\n"
            "  overrides:\n"
            "    - name: first-match\n"
            "      selectors:\n"
            "        - sc-aware-10\n"
            "      impairment:\n"
            "        loss: 25%\n"
            "    - name: later-match\n"
            "      selectors:\n"
            "        - sc-aware-10\n"
            "      impairment:\n"
            "        delay: 10ms\n"
        ),
        encoding="utf-8",
    )
    scenario = load_disruptor_scenario_file(scenario_file)

    plan = build_disruptor_tc_plan(
        interface_name="wlan0",
        devices=tuple(discovered_devices(1)),
        scenario=scenario,
    )

    assert [device_plan.policy_name for device_plan in plan.device_plans] == ["first-match"]
    assert plan.device_plans[0].impairment is not None
    assert plan.device_plans[0].impairment.delay is None
    assert plan.device_plans[0].impairment.loss == 25.0


def test_disruptor_tc_plan_represents_none_policy_as_unimpaired(
    tmp_path: Path,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        (
            "network_impairment:\n"
            "  default:\n"
            "    delay: 100ms\n"
            "  overrides:\n"
            "    - name: control\n"
            "      selectors:\n"
            "        - sc-aware-10\n"
            "      impairment: none\n"
        ),
        encoding="utf-8",
    )
    scenario = load_disruptor_scenario_file(scenario_file)

    plan = build_disruptor_tc_plan(
        interface_name="wlan0",
        devices=tuple(discovered_devices(1)),
        scenario=scenario,
    )

    assert plan.device_plans[0].policy_name == "control"
    assert plan.device_plans[0].impairment is None
    assert (
        "tc qdisc add dev wlan0 parent 1:10 handle 10: pfifo limit 1000"
        in plan.device_plans[0].commands
    )


def test_disruptor_tc_plan_returns_structured_warnings_for_unresolved_selectors(
    tmp_path: Path,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        (
            "network_impairment:\n"
            "  default:\n"
            "    delay: 100ms\n"
            "  overrides:\n"
            "    - name: missing-device\n"
            "      selectors:\n"
            "        - sc-aware-99\n"
            "      impairment:\n"
            "        loss: 25%\n"
        ),
        encoding="utf-8",
    )
    scenario = load_disruptor_scenario_file(scenario_file)

    plan = build_disruptor_tc_plan(
        interface_name="wlan0",
        devices=tuple(discovered_devices(1)),
        scenario=scenario,
    )

    assert [device_plan.policy_name for device_plan in plan.device_plans] == ["default"]
    assert [(warning.policy_name, warning.selector) for warning in plan.warnings] == [
        ("missing-device", "sc-aware-99")
    ]
