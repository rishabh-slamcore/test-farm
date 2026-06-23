"""Disruptor scenario and planning tests."""

import asyncio
import socket
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess
from threading import Event
from unittest.mock import Mock

import pytest
from zeroconf import ServiceInfo

from test_farm.disruptor import planning
from test_farm.disruptor.models import DiscoveredDevice, TCExecutionError, TCSetupError
from test_farm.disruptor.planning import (
    SubprocessExecutor,
    apply_disruptor_tc_plan,
    build_disruptor_tc_plan,
    discover_aware_devices,
    render_disruptor_dry_run,
    resolve_policy_name,
)
from test_farm.scenario import (
    DeviceNameMatch,
    DisruptorScenarioFileError,
    RegexMatch,
    ScenarioFileError,
    load_disruptor_scenario_file,
)


def _hawkbitc_service_info(
    *,
    name: str,
    address: str,
    vendor: str = "slamcore",
    product: str = "aware",
    extra_addresses: tuple[str, ...] = (),
) -> ServiceInfo:
    return ServiceInfo(
        "_hawkbitc._tcp.local.",
        name,
        addresses=[socket.inet_aton(ip_address) for ip_address in (address, *extra_addresses)],
        port=0,
        properties={b"vendor": vendor.encode(), b"product": product.encode()},
        server=f"{name.split('.', maxsplit=1)[0]}.local.",
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


def test_discover_aware_devices_manages_bounded_browse_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zeroconf = Mock()
    browser = Mock()
    zeroconf_factory = Mock(return_value=zeroconf)
    service_browser_factory = Mock(return_value=browser)

    monkeypatch.setattr("test_farm.disruptor.planning.Zeroconf", zeroconf_factory)
    monkeypatch.setattr(
        "test_farm.disruptor.planning.ServiceBrowser",
        service_browser_factory,
    )

    devices = discover_aware_devices()

    assert devices == ()
    zeroconf_factory.assert_called_once_with()
    service_browser_factory.assert_called_once()
    assert service_browser_factory.call_args.args[0] is zeroconf
    assert service_browser_factory.call_args.args[1] == planning._HAWKBITC_SERVICE_TYPE
    assert isinstance(
        service_browser_factory.call_args.args[2],
        planning._AwareDeviceListener,
    )
    browser.cancel.assert_called_once_with()
    zeroconf.close.assert_called_once_with()


def test_aware_device_listener_records_hawkbitc_service_info() -> None:
    service_info = _hawkbitc_service_info(
        name="sc-aware-jq3q0028._hawkbitc._tcp.local.",
        address="10.1.14.142",
    )
    unrelated_service_info = _hawkbitc_service_info(
        name="printer._hawkbitc._tcp.local.",
        address="10.1.14.200",
        vendor="elsewhere",
    )

    class FakeZeroconf:
        def __init__(self) -> None:
            self.service_infos = {
                service_info.name: service_info,
                unrelated_service_info.name: unrelated_service_info,
            }

        def get_service_info(self, type_: str, name: str) -> ServiceInfo | None:
            del type_
            return self.service_infos.get(name)

    listener = planning._AwareDeviceListener()
    zc = FakeZeroconf()

    listener.add_service(
        zc,  # type:ignore[arg-type]
        planning._HAWKBITC_SERVICE_TYPE,
        service_info.name,
    )
    listener.add_service(
        zc,  # type:ignore[arg-type]
        planning._HAWKBITC_SERVICE_TYPE,
        unrelated_service_info.name,
    )

    assert listener.devices() == (
        DiscoveredDevice(device_id="sc-aware-jq3q0028", ip_address="10.1.14.142"),
    )


def test_discover_aware_devices_uses_first_name_component_and_first_address() -> None:
    service_info = _hawkbitc_service_info(
        name="linux-5._hawkbitc._tcp.local.",
        address="10.1.13.93",
        extra_addresses=("10.1.13.94",),
    )

    device = planning._discovered_device_from_service(
        name="linux-5._hawkbitc._tcp.local.",
        info=service_info,
    )

    assert device == DiscoveredDevice(device_id="linux-5", ip_address="10.1.13.93")


def test_discover_aware_devices_ignores_non_aware_hawkbitc_services() -> None:
    service_info = _hawkbitc_service_info(
        name="other._hawkbitc._tcp.local.",
        address="10.1.13.93",
        product="not-aware",
    )

    device = planning._discovered_device_from_service(
        name="other._hawkbitc._tcp.local.",
        info=service_info,
    )

    assert device is None


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
    assert [resolve_policy_name(node.device, plan.scenario) for node in plan.routing_tree] == [
        "default",
        "default",
    ]
    assert [node.qdisc.impairment for node in plan.routing_tree] == [
        scenario.default_impairment,
        scenario.default_impairment,
    ]
    assert [node.classid for node in plan.routing_tree] == ["1:10", "1:20"]
    commands = plan.routing_tree.pending_commands()
    assert (
        "tc filter add dev wlan0 parent 1: protocol ip prio 1 u32 match ip dst "
        "192.0.2.11/32 flowid 1:20"
    ) in commands


def test_build_disruptor_tc_plan_bubbles_duplicate_device_id_setup_error(
    tmp_path: Path,
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        ("network_impairment:\n" "  default:\n" "    delay: 100ms\n"),
        encoding="utf-8",
    )
    scenario = load_disruptor_scenario_file(scenario_file)
    duplicate_devices = (
        DiscoveredDevice(device_id="sc-aware-10", ip_address="192.0.2.10"),
        DiscoveredDevice(device_id="sc-aware-10", ip_address="192.0.2.11"),
    )

    with pytest.raises(TCSetupError, match="Duplicate device id discovered: sc-aware-10"):
        build_disruptor_tc_plan(
            interface_name="wlan0",
            devices=duplicate_devices,
            scenario=scenario,
        )


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

    assert [resolve_policy_name(node.device, plan.scenario) for node in plan.routing_tree] == [
        "first-match"
    ]
    node = next(iter(plan.routing_tree))
    assert node.qdisc.impairment is not None
    assert node.qdisc.impairment.delay is None
    assert node.qdisc.impairment.loss == 25.0


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

    assert [resolve_policy_name(node.device, plan.scenario) for node in plan.routing_tree] == [
        "batch",
        "batch",
        "default",
    ]
    node = next(iter(plan.routing_tree))
    assert node.qdisc.impairment is not None
    assert node.qdisc.impairment.loss == 25.0
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

    node = next(iter(plan.routing_tree))
    assert resolve_policy_name(node.device, plan.scenario) == "control"
    assert node.qdisc.impairment is None
    commands = plan.routing_tree.pending_commands()
    assert "tc qdisc add dev wlan0 parent 1:10 handle 10: pfifo limit 10000" in commands


def test_disruptor_tc_plan_renders_bandwidth_only_policy_through_tbf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    monkeypatch.setattr("test_farm.disruptor.device_tree.read_mtu", lambda interface: 1500)
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        ("network_impairment:\n" "  default:\n" "    bandwidth_limit: 1mbit\n"),
        encoding="utf-8",
    )
    scenario = load_disruptor_scenario_file(scenario_file)

    plan = build_disruptor_tc_plan(
        interface_name="wlan0",
        devices=tuple(discovered_devices(1)),
        scenario=scenario,
    )

    commands = plan.routing_tree.pending_commands()
    assert (
        "tc qdisc add dev wlan0 parent 1:10 handle 10: "
        "tbf rate 1mbit burst 6000 latency 50ms"
    ) in commands
    assert not any("netem" in command for command in commands)


def test_disruptor_tc_plan_renders_bandwidth_with_delay_and_loss_through_tbf_then_netem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    monkeypatch.setattr("test_farm.disruptor.device_tree.read_mtu", lambda interface: 1500)
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        (
            "network_impairment:\n"
            "  default:\n"
            "    delay: 100ms\n"
            "    loss: 5%\n"
            "    bandwidth_limit: 1mbit\n"
        ),
        encoding="utf-8",
    )
    scenario = load_disruptor_scenario_file(scenario_file)

    plan = build_disruptor_tc_plan(
        interface_name="wlan0",
        devices=tuple(discovered_devices(1)),
        scenario=scenario,
    )

    commands = plan.routing_tree.pending_commands()
    assert commands[-4:] == (
        "tc class add dev wlan0 parent 1:1 classid 1:10 htb rate 1000mbit",
        "tc qdisc add dev wlan0 parent 1:10 handle 10: "
        "tbf rate 1mbit burst 6000 latency 50ms",
        "tc qdisc add dev wlan0 parent 10: handle 10:1 netem delay 100ms loss 5%",
        "tc filter add dev wlan0 parent 1: protocol ip prio 1 u32 match ip dst "
        "192.0.2.10/32 flowid 1:10",
    )


def test_render_disruptor_dry_run_is_non_destructive(
    tmp_path: Path,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        ("network_impairment:\n" "  default:\n" "    delay: 100ms\n"),
        encoding="utf-8",
    )
    scenario = load_disruptor_scenario_file(scenario_file)
    plan = build_disruptor_tc_plan(
        interface_name="wlan0",
        devices=tuple(discovered_devices(1)),
        scenario=scenario,
    )

    first_render = render_disruptor_dry_run(plan)
    second_render = render_disruptor_dry_run(plan)

    assert second_render == first_render
    assert "tc qdisc add dev wlan0 parent 1:10 handle 10: netem delay 100ms" in first_render
    assert "tc qdisc add dev wlan0 parent 1:10 handle 10: netem delay 100ms" in (
        "\n".join(plan.routing_tree.pending_commands())
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

    assert [resolve_policy_name(node.device, plan.scenario) for node in plan.routing_tree] == [
        "default"
    ]
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

    assert [resolve_policy_name(node.device, plan.scenario) for node in plan.routing_tree] == [
        "partial"
    ]
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

    assert [resolve_policy_name(node.device, plan.scenario) for node in plan.routing_tree] == [
        "default"
    ]
    assert [(warning.policy_name,) for warning in plan.warnings] == [("missing-batch",)]


class RecordingTCExecutor:
    def __init__(self, applied_event: Event) -> None:
        self.operations: list[tuple[str, str]] = []
        self._applied_event = applied_event

    def delete_root_qdisc(self, interface_name: str) -> None:
        self.operations.append(("delete-root", interface_name))

    def run(self, command: str) -> None:
        self.operations.append(("run", command))
        if command.startswith("tc filter add"):
            self._applied_event.set()


def test_apply_disruptor_tc_plan_cleans_up_applies_blocks_and_tears_down(
    tmp_path: Path,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    asyncio.run(
        _assert_apply_disruptor_tc_plan_cleans_up_applies_blocks_and_tears_down(
            tmp_path=tmp_path,
            discovered_devices=discovered_devices,
        )
    )


async def _assert_apply_disruptor_tc_plan_cleans_up_applies_blocks_and_tears_down(
    *,
    tmp_path: Path,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        ("network_impairment:\n" "  default:\n" "    delay: 100ms\n"),
        encoding="utf-8",
    )
    scenario = load_disruptor_scenario_file(scenario_file)
    plan = build_disruptor_tc_plan(
        interface_name="wlan0",
        devices=tuple(discovered_devices(1)),
        scenario=scenario,
    )
    applied_event = Event()
    stop_event = Event()
    executor = RecordingTCExecutor(applied_event=applied_event)
    lifecycle_task = asyncio.create_task(
        asyncio.to_thread(
            apply_disruptor_tc_plan,
            plan,
            executor=executor,
            stop_event=stop_event,
        )
    )
    try:
        assert await asyncio.to_thread(applied_event.wait, timeout=1)
        assert not lifecycle_task.done()
        assert executor.operations[0] == ("delete-root", "wlan0")
        assert executor.operations.count(("delete-root", "wlan0")) == 1
        assert (
            "run",
            "tc qdisc add dev wlan0 parent 1:10 handle 10: netem delay 100ms",
        ) in executor.operations
    finally:
        stop_event.set()
        await asyncio.wait_for(lifecycle_task, timeout=1)

    assert lifecycle_task.done()
    assert executor.operations[-1] == ("delete-root", "wlan0")
    assert executor.operations.count(("delete-root", "wlan0")) == 2


def test_subprocess_disruptor_tc_executor_explains_missing_net_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_tc_command(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> CompletedProcess[str]:
        del capture_output, text, check
        return CompletedProcess(
            args=args,
            returncode=2,
            stdout="",
            stderr="RTNETLINK answers: Operation not permitted\n",
        )

    monkeypatch.setattr("test_farm.disruptor.planning.subprocess.run", deny_tc_command)
    executor = SubprocessExecutor()

    with pytest.raises(
        TCExecutionError,
        match="CAP_NET_ADMIN",
    ):
        executor.delete_root_qdisc("wlan0")


def test_subprocess_disruptor_tc_executor_ignores_absent_zero_handle_root_qdisc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_tc_command(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> CompletedProcess[str]:
        del args, capture_output, text, check
        return CompletedProcess(
            args=["tc", "qdisc", "del", "dev", "wlan0", "root"],
            returncode=2,
            stdout="",
            stderr="Error: Cannot delete qdisc with handle of zero.\n",
        )

    monkeypatch.setattr("test_farm.disruptor.planning.subprocess.run", deny_tc_command)
    executor = SubprocessExecutor()

    executor.delete_root_qdisc("wlan0")
