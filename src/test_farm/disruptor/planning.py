"""Disruptor scenario parsing and tc planning."""

import shlex
import subprocess
from threading import Event
from typing import Protocol

from test_farm.disruptor.device_tree import HandleManager, HTBTree, allocate_qdisc
from test_farm.disruptor.models import ResolverWarning, TCDevicePlan, TCExecutionError, TCPlan
from test_farm.models import DEVICE_VARIANTS, DiscoveredDevice
from test_farm.network_impairment import NetworkImpairment
from test_farm.scenario import DisruptorScenario, Selector


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
        root_tc_tree.add_node(qdisc=qdisc, device=device)

    if not devices:
        qdisc = allocate_qdisc(scenario.default_impairment)
        root_tc_tree.add_default(qdisc=qdisc)

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
        if node.device:
            lines.append(
                f"{node.device.device_id} {node.device.ip_address} -> {resolve_policy_name(node.device, plan.scenario)}"
            )
        else:
            lines.append("default policy applied")
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


def _does_device_match_selector(device: DiscoveredDevice, selector: Selector) -> bool:
    return selector.accept(device)


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
    for override in scenario.overrides:
        if not any(
            _does_device_match_selector(device, override.selector) for device in devices
        ):
            warnings.append(
                ResolverWarning(policy_name=override.name, selector=override.selector)
            )
    return tuple(warnings)
