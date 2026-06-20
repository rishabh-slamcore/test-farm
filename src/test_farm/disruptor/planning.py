"""Disruptor scenario parsing and dry-run tc planning."""

from test_farm.disruptor.device_tree import HandleManager, HTBTree, allocate_qdisc
from test_farm.disruptor.models import (
    DiscoveredDevice,
    DisruptorResolverWarning,
    DisruptorTcDevicePlan,
    DisruptorTcPlan,
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


def build_disruptor_tc_plan(
    *,
    interface_name: str,
    devices: tuple[DiscoveredDevice, ...],
    scenario: DisruptorScenario,
    mtu: int = 1500,
) -> DisruptorTcPlan:
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

    return DisruptorTcPlan(
        interface_name=interface_name,
        routing_tree=root_tc_tree,
        scenario=scenario,
        warnings=_resolve_warnings(scenario=scenario, devices=devices),
    )


def render_disruptor_dry_run(plan: DisruptorTcPlan) -> str:
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


def apply_disruptor_tc_plan(plan: DisruptorTcPlan) -> None:
    """Apply a Disruptor tc plan.

    :param plan: Typed tc plan to apply.
    :raises NotImplementedError: Always in the first dry-run-only slice.
    """

    del plan
    raise NotImplementedError("Only Disruptor --dry-run is implemented.")


def discover_aware_devices() -> tuple[DiscoveredDevice, ...]:
    """Discover Slamcore Aware devices visible to the Disruptor.

    :returns: Discovered devices. Real mDNS discovery is outside this dry-run slice.
    """

    return ()


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
) -> tuple[DisruptorResolverWarning, ...]:
    warnings: list[DisruptorResolverWarning] = []
    discovered_device_ids = set(device.device_id for device in devices)
    for override in scenario.overrides:
        if (
            override.selector.unmatched_devices(discovered_device_names=discovered_device_ids)
            == discovered_device_ids
        ):
            warnings.append(
                DisruptorResolverWarning(policy_name=override.name, selector=override.selector)
            )
    return tuple(warnings)
