"""Disruptor scenario parsing and tc planning."""

import shlex
import socket
import subprocess
import time
from threading import Event
from typing import Protocol

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

from test_farm.disruptor.device_tree import HandleManager, HTBTree, allocate_qdisc
from test_farm.disruptor.models import (
    DEVICE_VARIANTS,
    DiscoveredDevice,
    ResolverWarning,
    TCDevicePlan,
    TCExecutionError,
    TCPlan,
)
from test_farm.network_impairment import (
    NetworkImpairment,
    _format_bandwidth_limit,
    _format_delay,
    _format_loss,
    compute_burst,
    netem_arguments,
    validate_burst,
)
from test_farm.scenario import DisruptorScenario, Selector

_HAWKBITC_SERVICE_TYPE = "_hawkbitc._tcp.local."
_AWARE_DISCOVERY_WINDOW_SECONDS = 6.0


class TCExecutor(Protocol):
    """Executor for applying rendered Disruptor tc commands."""

    def delete_root_qdisc(self, interface_name: str) -> None:
        """Delete the root qdisc from an interface if one exists."""

    def run(self, command: str) -> None:
        """Run one rendered tc command."""


class SubprocessExecutor:
    """Apply Disruptor tc commands through the local ``tc`` binary."""

    def delete_root_qdisc(self, interface_name: str) -> None:
        command = ["tc", "qdisc", "del", "dev", interface_name, "root"]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0 or _tc_root_qdisc_absent(result.stderr):
            return

        _raise_tc_execution_error(command=command, result=result)

    def run(self, command: str) -> None:
        args = shlex.split(command)
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            _raise_tc_execution_error(command=args, result=result)


def _raise_tc_execution_error(
    *,
    command: list[str],
    result: subprocess.CompletedProcess[str],
) -> None:
    stderr = result.stderr.strip()
    stdout = result.stdout.strip()
    detail = stderr or stdout or "tc exited with no diagnostic output"
    if "Operation not permitted" in stderr:
        detail = (
            f"{detail}\n"
            "Disruptor requires CAP_NET_ADMIN to modify tc state. "
            "Run as root, grant CAP_NET_ADMIN to the process, or run the container "
            "with NET_ADMIN capability."
        )

    raise TCExecutionError(
        f"tc command failed with exit code {result.returncode}: {' '.join(command)}\n{detail}"
    )


def _tc_root_qdisc_absent(stderr: str) -> bool:
    return (
        "No such file or directory" in stderr
        or "Cannot delete qdisc with handle of zero" in stderr
    )


def build_disruptor_tc_plan(
    *,
    interface_name: str,
    devices: tuple[DiscoveredDevice, ...],
    scenario: DisruptorScenario,
    mtu: int = 1500,
) -> TCPlan:
    """Resolve a parsed Disruptor scenario to a typed tc plan.

    :param interface_name: Client-facing NIC name.
    :param devices: Discovered devices to classify.
    :param scenario: Parsed Disruptor Scenario File.
    :param mtu: Interface MTU used for TBF burst planning.
    :returns: Typed tc plan object.
    """
    HandleManager.clear()
    HandleManager.setup(devices)
    root_tc_tree = HTBTree(interface_name)

    for device in devices:
        impairment = _resolve_impairment(device, scenario)
        qdisc = allocate_qdisc(impairment)
        root_tc_tree.add_node(device=device, qdisc=qdisc)

    return TCPlan(
        interface_name=interface_name,
        routing_tree=root_tc_tree,
        scenario=scenario,
        warnings=_resolve_warnings(scenario=scenario, devices=devices),
    )


def render_disruptor_dry_run(plan: TCPlan) -> str:
    """Render a human-readable dry-run plan.

    :param plan: Typed tc plan to render.
    :returns: Human-readable dry-run output.
    """

    lines = [f"Disruptor dry-run plan for interface {plan.interface_name}"]
    for node in plan.routing_tree:
        lines.append(
            f"{node.device.device_id} {node.device.ip_address} -> {resolve_policy_name(node.device, plan.scenario)}"
        )
    for warning in plan.warnings:
        lines.append(
            f"warning: selector in policy "
            f"{warning.policy_name} did not match a discovered device"
        )
    lines.append("tc commands:")
    lines.extend(plan.routing_tree.pending_commands())
    return "\n".join(lines) + "\n"


def apply_disruptor_tc_plan(
    plan: TCPlan,
    *,
    executor: TCExecutor | None = None,
    stop_event: Event | None = None,
) -> None:
    """Apply a Disruptor tc plan.

    :param plan: Typed tc plan to apply.
    :param executor: Executor used to mutate tc state.
    :param stop_event: Optional event that ends the blocking lifecycle when set.
    """

    tc_executor = executor or SubprocessExecutor()
    lifecycle_stop = stop_event or Event()
    try:
        tc_executor.delete_root_qdisc(plan.interface_name)
        for command in plan.routing_tree.pending_commands():
            tc_executor.run(command)
        lifecycle_stop.wait()
    finally:
        tc_executor.delete_root_qdisc(plan.interface_name)


def discover_aware_devices() -> tuple[DiscoveredDevice, ...]:
    """Discover Slamcore Aware devices visible to the Disruptor.

    :returns: Discovered devices found during one bounded mDNS browse.
    """

    listener = _AwareDeviceListener()
    zeroconf = Zeroconf()
    browser = ServiceBrowser(zeroconf, _HAWKBITC_SERVICE_TYPE, listener)
    try:
        time.sleep(_AWARE_DISCOVERY_WINDOW_SECONDS)
        return listener.devices()
    finally:
        browser.cancel()
        zeroconf.close()


class _AwareDeviceListener(ServiceListener):
    """Collect Slamcore Aware hawkBit client services reported by Zeroconf."""

    def __init__(self) -> None:
        self._devices: dict[str, DiscoveredDevice] = {}

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._record_service(zc, type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._record_service(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        device_id = _service_device_id(name)
        if device_id:
            self._devices.pop(device_id, None)

    def devices(self) -> tuple[DiscoveredDevice, ...]:
        return tuple(sorted(self._devices.values(), key=lambda device: device.device_id))

    def _record_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        device = _discovered_device_from_service(name=name, info=info)
        if device is None:
            return

        self._devices[device.device_id] = device


def _discovered_device_from_service(
    *,
    name: str,
    info: ServiceInfo | None,
) -> DiscoveredDevice | None:
    if info is None:
        return None

    txt = _decode_txt_properties(info.properties)
    if txt.get("vendor") != "slamcore" or txt.get("product") != "aware":
        return None

    variant = txt.get("variant")
    if variant not in DEVICE_VARIANTS:
        return None

    addresses = _decode_addresses(info.addresses)
    if not addresses:
        return None

    device_id = _service_device_id(name)
    if not device_id:
        return None

    return DiscoveredDevice(device_id=device_id, ip_address=addresses[0], variant=variant)


def _service_device_id(name: str) -> str:
    return name.split(".", maxsplit=1)[0]


def _decode_txt_properties(properties: dict[bytes, bytes | None]) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in properties.items():
        if value is None:
            continue
        decoded[key.decode(errors="ignore")] = value.decode(errors="ignore")
    return decoded


def _decode_addresses(addresses: list[bytes]) -> list[str]:
    decoded: list[str] = []
    for address in addresses:
        try:
            family = socket.AF_INET6 if len(address) == 16 else socket.AF_INET
            decoded.append(socket.inet_ntop(family, address))
        except ValueError:
            continue
    return decoded


def _does_device_match_selector(device: DiscoveredDevice, selector: Selector) -> bool:
    return selector.accept(device.device_id)


def resolve_policy_name(device: DiscoveredDevice, scenario: DisruptorScenario) -> str:
    for override in scenario.overrides:
        if _does_device_match_selector(device, override.selector):
            return override.name

    return "default"


def _resolve_impairment(
    device: DiscoveredDevice,
    scenario: DisruptorScenario,
) -> NetworkImpairment | None:
    for override in scenario.overrides:
        if _does_device_match_selector(device, override.selector):
            return override.impairment

    return scenario.default_impairment


def _resolve_warnings(
    *,
    scenario: DisruptorScenario,
    devices: tuple[DiscoveredDevice, ...],
) -> tuple[ResolverWarning, ...]:
    warnings: list[ResolverWarning] = []
    discovered_device_ids = set(device.device_id for device in devices)
    for override in scenario.overrides:
        if (
            override.selector.unmatched_devices(discovered_device_names=discovered_device_ids)
            == discovered_device_ids
        ):
            warnings.append(
                ResolverWarning(policy_name=override.name, selector=override.selector)
            )
    return tuple(warnings)
