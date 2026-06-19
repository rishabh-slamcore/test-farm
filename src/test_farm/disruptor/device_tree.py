import logging
from dataclasses import dataclass
from typing import ClassVar, Protocol, Sequence

from test_farm.disruptor.models import DiscoveredDevice, TCSetupError
from test_farm.network_impairment import (
    NetworkImpairment,
    _format_bandwidth_limit,
    compute_burst,
    netem_arguments,
    read_mtu,
    validate_burst,
)

logger = logging.getLogger(__name__)


class HandleManager:

    _handles: ClassVar[dict[str, int]] = {}

    @classmethod
    def setup(cls, devices: Sequence[DiscoveredDevice]) -> None:
        total_devices = len(devices)
        for index, device in enumerate(devices, start=10 * total_devices):
            cls._handles[device.device_id] = index

    @classmethod
    def get_handle(cls, device_id: str) -> str:
        return f"{cls._handles[device_id]}:"


class LeafQDisc(Protocol):
    def command(self, interface: str, parent: str) -> Sequence[str]: ...


class TBFQdisc:
    def __init__(self, impairment: NetworkImpairment):
        assert impairment.bandwidth_limit is not None
        self.bps = impairment.bandwidth_limit

    def command(self, interface_name: str, parent: str, device_id: str) -> Sequence[str]:
        mtu = read_mtu(interface_name)
        burst = compute_burst(self.bps, mtu)
        validate_burst(burst, mtu, self.bps)
        latency = "50ms"  # packets waiting for more than latency will be dropped from queue
        try:
            handle = HandleManager.get_handle(device_id)
        except KeyError:
            raise TCSetupError(f"No handle available for {device_id}.")
        return (
            f"tc qdisc add dev {interface_name} parent {parent} handle {handle}"
            f"tbf rate {_format_bandwidth_limit(self.bps)} burst {burst} latency {latency}"
        )


class NetemQdisc:
    def __init__(self, impairment: NetworkImpairment):
        self.impairment = impairment

    def command(self, interface_name: str, parent: str, device_id: str) -> Sequence[str]:
        args = " ".join(netem_arguments(self.impairment))
        try:
            handle = HandleManager.get_handle(device_id)
        except KeyError:
            raise TCSetupError(f"No handle available for {device_id}.")
        return (
            f"tc qdisc add dev {interface_name} parent {parent} handle {handle}"
            f"netem {args}"
        )


class PFiFoQdisc:
    def __init__(self, _: NetworkImpairment): ...

    def command(self, interface_name: str, parent: str, device_id: str) -> Sequence[str]:
        try:
            handle = HandleManager.get_handle(device_id)
        except KeyError:
            raise TCSetupError(f"No handle available for {device_id}.")
        return (
            f"tc qdisc add dev {interface_name} parent {parent} handle {handle}"
            f"pfifo limit 10000"  # limit <number> of messages queue can hold
        )
