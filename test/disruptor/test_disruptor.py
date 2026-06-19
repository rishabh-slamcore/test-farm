"""Disruptor scenario and planning tests."""

from collections.abc import Callable
from pathlib import Path

import pytest

from test_farm.disruptor.models import DiscoveredDevice
from test_farm.disruptor.planning import build_disruptor_tc_plan
from test_farm.scenario import (
    DeviceNameMatch,
    DisruptorScenarioFileError,
    RegexMatch,
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
            "      device_match:\n"
            "        - sc-aware-11\n"
            "        - sc-aware-13\n"
            "      impairment:\n"
            "        loss: 25%\n"
            "    - name: control\n"
            "      device_match:\n"
            "        - sc-aware-12\n"
            "      impairment: none\n"
        ),
        encoding="utf-8",
    )

    scenario = load_disruptor_scenario_file(scenario_file)

    assert [override.name for override in scenario.overrides] == ["high-loss", "control"]
    assert isinstance(scenario.overrides[0].selector, DeviceNameMatch)
    assert scenario.overrides[0].selector.device_names == set(["sc-aware-11", "sc-aware-13"])
    assert scenario.overrides[0].impairment is not None
    assert scenario.overrides[0].impairment.loss == 25.0
    assert isinstance(scenario.overrides[1].selector, DeviceNameMatch)
    assert scenario.overrides[1].selector.device_names == set(
        ["sc-aware-12"],
    )
    assert scenario.overrides[1].impairment is None


def test_load_disruptor_scenario_file_parses_regex_override(tmp_path: Path) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        (
            "network_impairment:\n"
            "  default:\n"
            "    delay: 100ms\n"
            "  overrides:\n"
            "    - name: batch\n"
            "      regex_match: '^sc-aware-1[01]$'\n"
            "      impairment:\n"
            "        loss: 25%\n"
        ),
        encoding="utf-8",
    )

    scenario = load_disruptor_scenario_file(scenario_file)

    assert isinstance(scenario.overrides[0].selector, RegexMatch)
    assert scenario.overrides[0].selector.pattern == "^sc-aware-1[01]$"
    assert scenario.overrides[0].selector.accept("sc-aware-10")
    assert not scenario.overrides[0].selector.accept("sc-aware-12")


@pytest.mark.parametrize(
    ("override_yaml", "error_pattern"),
    [
        (
            (
                "      selectors:\n"
                "        - sc-aware-10\n"
                "      impairment:\n"
                "        loss: 25%\n"
            ),
            "unknown fields: network_impairment.overrides\\[0\\].selectors",
        ),
        (
            ("      impairment:\n" "        loss: 25%\n"),
            (
                "must set exactly one of network_impairment.overrides\\[0\\].device_match "
                "or network_impairment.overrides\\[0\\].regex_match"
            ),
        ),
        (
            (
                "      device_match:\n"
                "        - sc-aware-10\n"
                "      regex_match: '^sc-aware-1[01]$'\n"
                "      impairment:\n"
                "        loss: 25%\n"
            ),
            (
                "must set exactly one of network_impairment.overrides\\[0\\].device_match "
                "or network_impairment.overrides\\[0\\].regex_match"
            ),
        ),
        (
            ("      device_match: []\n" "      impairment:\n" "        loss: 25%\n"),
            "must set network_impairment.overrides\\[0\\].device_match to a non-empty sequence",
        ),
        (
            (
                "      device_match:\n"
                "        - ''\n"
                "      impairment:\n"
                "        loss: 25%\n"
            ),
            (
                "must set network_impairment.overrides\\[0\\].device_match\\[0\\] "
                "to a non-empty string"
            ),
        ),
        (
            ("      regex_match: '['\n" "      impairment:\n" "        loss: 25%\n"),
            "must set network_impairment.overrides\\[0\\].regex_match to a valid regular expression",
        ),
    ],
)
def test_load_disruptor_scenario_file_rejects_invalid_selector_shapes(
    tmp_path: Path,
    override_yaml: str,
    error_pattern: str,
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        (
            "network_impairment:\n"
            "  default:\n"
            "    delay: 100ms\n"
            "  overrides:\n"
            "    - name: invalid\n"
            f"{override_yaml}"
        ),
        encoding="utf-8",
    )

    with pytest.raises(DisruptorScenarioFileError, match=error_pattern):
        load_disruptor_scenario_file(scenario_file)


def test_load_disruptor_scenario_file_assigns_name_to_unnamed_override(
    tmp_path: Path,
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        (
            "network_impairment:\n"
            "  default:\n"
            "    delay: 100ms\n"
            "  overrides:\n"
            "    - device_match:\n"
            "        - sc-aware-10\n"
            "      impairment:\n"
            "        loss: 25%\n"
        ),
        encoding="utf-8",
    )

    scenario = load_disruptor_scenario_file(scenario_file)

    assert scenario.overrides[0].name == "override-0"
    assert isinstance(scenario.overrides[0].selector, DeviceNameMatch)
    assert scenario.overrides[0].selector.device_names == set(
        ["sc-aware-10"],
    )


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
            "      device_match:\n"
            "        - sc-aware-10\n"
            "      impairment:\n"
            "        loss: 25%\n"
            "    - name: later-match\n"
            "      device_match:\n"
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


def test_disruptor_tc_plan_uses_regex_override(
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
            "    - name: batch\n"
            "      regex_match: '^sc-aware-1[01]$'\n"
            "      impairment:\n"
            "        loss: 25%\n"
        ),
        encoding="utf-8",
    )
    scenario = load_disruptor_scenario_file(scenario_file)

    plan = build_disruptor_tc_plan(
        interface_name="wlan0",
        devices=tuple(discovered_devices(3)),
        scenario=scenario,
    )

    assert [device_plan.policy_name for device_plan in plan.device_plans] == [
        "batch",
        "batch",
        "default",
    ]
    assert plan.device_plans[0].impairment is not None
    assert plan.device_plans[0].impairment.loss == 25.0
    assert plan.warnings == ()


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
            "      device_match:\n"
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
            "      device_match:\n"
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
    assert [(warning.policy_name) for warning in plan.warnings] == [("missing-device")]


def test_disruptor_tc_plan_warns_only_for_device_names(
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
            "    - name: partial\n"
            "      device_match:\n"
            "        - sc-aware-10\n"
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

    assert [device_plan.policy_name for device_plan in plan.device_plans] == ["partial"]
    assert [(warning.policy_name, warning.selector) for warning in plan.warnings] == []


def test_disruptor_tc_plan_warns_for_unmatched_regex_selector(
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
            "    - name: missing-batch\n"
            "      regex_match: '^warehouse-[0-9]+$'\n"
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
    assert [(warning.policy_name,) for warning in plan.warnings] == [("missing-batch",)]
