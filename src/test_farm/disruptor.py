"""Disruptor scenario parsing and dry-run tc planning."""

from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from test_farm.network_impairment import (
    NetworkImpairment,
    _format_bandwidth_limit,
    _format_delay,
    _format_loss,
    compute_burst,
    validate_burst,
)


@dataclass(frozen=True)
class DiscoveredDevice:
    """A real Slamcore Aware device discovered by the Disruptor."""

    device_id: str
    ip_address: str


@dataclass(frozen=True)
class DisruptorTcDevicePlan:
    """A device-specific resolved tc plan lane."""

    device: DiscoveredDevice
    policy_name: str
    impairment: NetworkImpairment
    class_id: str
    commands: tuple[str, ...]


@dataclass(frozen=True)
class DisruptorTcPlan:
    """A typed tc plan for one dry-run Disruptor invocation."""

    interface_name: str
    device_plans: tuple[DisruptorTcDevicePlan, ...]
    commands: tuple[str, ...]


def build_default_disruptor_tc_plan(
    *,
    interface_name: str,
    devices: tuple[DiscoveredDevice, ...],
    default_impairment: NetworkImpairment,
    mtu: int = 1500,
) -> DisruptorTcPlan:
    """Resolve every discovered device to the default impairment policy.

    :param interface_name: Client-facing NIC name.
    :param devices: Discovered devices to impair.
    :param default_impairment: Impairment applied to every discovered device.
    :param mtu: Interface MTU used for TBF burst planning.
    :returns: Typed tc plan object.
    """

    root_commands = [
        f"tc qdisc add dev {interface_name} root handle 1: htb default 99",
        f"tc class add dev {interface_name} parent 1: classid 1:1 htb rate 1000mbit",
        f"tc class add dev {interface_name} parent 1:1 classid 1:99 htb rate 1000mbit",
        f"tc qdisc add dev {interface_name} parent 1:99 handle 99: pfifo limit 1000",
    ]
    device_plans = tuple(
        _build_default_device_plan(
            interface_name=interface_name,
            device=device,
            device_index=device_index,
            default_impairment=default_impairment,
            mtu=mtu,
        )
        for device_index, device in enumerate(devices, start=1)
    )
    commands = tuple(root_commands) + tuple(
        command for device_plan in device_plans for command in device_plan.commands
    )
    return DisruptorTcPlan(
        interface_name=interface_name,
        device_plans=device_plans,
        commands=commands,
    )


def render_disruptor_dry_run(plan: DisruptorTcPlan) -> str:
    """Render a human-readable dry-run plan.

    :param plan: Typed tc plan to render.
    :returns: Human-readable dry-run output.
    """

    lines = [f"Disruptor dry-run plan for interface {plan.interface_name}"]
    for device_plan in plan.device_plans:
        lines.append(
            f"{device_plan.device.device_id} {device_plan.device.ip_address} -> {device_plan.policy_name}"
        )
    lines.append("tc commands:")
    lines.extend(plan.commands)
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


def _build_default_device_plan(
    *,
    interface_name: str,
    device: DiscoveredDevice,
    device_index: int,
    default_impairment: NetworkImpairment,
    mtu: int,
) -> DisruptorTcDevicePlan:
    class_minor = device_index * 10
    class_id = f"1:{class_minor}"
    qdisc_handle = f"{class_minor}:"
    netem_handle = f"{class_minor * 10}:"
    commands = [
        f"tc class add dev {interface_name} parent 1:1 classid {class_id} htb rate 1000mbit",
    ]

    if default_impairment.bandwidth_limit is None:
        commands.append(
            f"tc qdisc add dev {interface_name} parent {class_id} handle {qdisc_handle} "
            f"netem {' '.join(_netem_arguments(default_impairment))}"
        )
    else:
        burst = compute_burst(default_impairment.bandwidth_limit, mtu)
        validate_burst(burst, mtu, default_impairment.bandwidth_limit)
        commands.append(
            f"tc qdisc add dev {interface_name} parent {class_id} handle {qdisc_handle} "
            f"tbf rate {_format_bandwidth_limit(default_impairment.bandwidth_limit)} "
            f"burst {burst} latency 50ms"
        )
        netem_arguments = _netem_arguments(default_impairment)
        if netem_arguments:
            commands.append(
                f"tc qdisc add dev {interface_name} parent {qdisc_handle} handle {netem_handle} "
                f"netem {' '.join(netem_arguments)}"
            )

    commands.append(
        f"tc filter add dev {interface_name} parent 1: protocol ip prio {device_index} "
        f"u32 match ip dst {device.ip_address}/32 flowid {class_id}"
    )
    return DisruptorTcDevicePlan(
        device=device,
        policy_name="default",
        impairment=default_impairment,
        class_id=class_id,
        commands=tuple(commands),
    )


def _netem_arguments(network_impairment: NetworkImpairment) -> list[str]:
    arguments: list[str] = []
    if network_impairment.delay is not None:
        arguments.extend(["delay", _format_delay(network_impairment.delay)])
    if network_impairment.loss is not None:
        arguments.extend(["loss", _format_loss(network_impairment.loss)])
    return arguments
