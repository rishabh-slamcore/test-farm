import logging
from typing import ClassVar, Iterator, Protocol, Sequence, Tuple

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
        seen_device_ids: set[str] = set()
        for index, device in enumerate(devices, start=1):
            if device.device_id in seen_device_ids:
                raise TCSetupError(f"Duplicate device id discovered: {device.device_id}")
            seen_device_ids.add(device.device_id)
            cls._handles[device.device_id] = index * 10

    @classmethod
    def class_minor(cls, device_id: str) -> int:
        return cls._handles[device_id]

    @classmethod
    def classid(cls, device_id: str) -> str:
        return f"1:{cls.class_minor(device_id)}"

    @classmethod
    def handle(cls, device_id: str) -> str:
        return f"{cls.class_minor(device_id)}:"

    @classmethod
    def clear(cls) -> None:
        cls._handles.clear()


class LeafQDisc(Protocol):
    impairment: NetworkImpairment | None

    def command(self, interface_name: str, parent: str, device_id: str) -> Tuple[str, ...]: ...


class TBFQdisc:
    def __init__(self, impairment: NetworkImpairment):
        self.impairment: NetworkImpairment | None = impairment

    def command(self, interface_name: str, parent: str, device_id: str) -> Tuple[str, ...]:
        assert self.impairment is not None
        bps = self.impairment.bandwidth_limit
        assert bps is not None
        mtu = read_mtu(interface_name)
        burst = compute_burst(bps, mtu)
        validate_burst(burst, mtu, bps)
        latency = "50ms"  # packets waiting for more than latency will be dropped from queue
        try:
            handle = HandleManager.handle(device_id)
        except KeyError:
            raise TCSetupError(f"No handle available for {device_id}.")
        return (
            f"tc qdisc add dev {interface_name} parent {parent} handle {handle} "
            f"tbf rate {_format_bandwidth_limit(bps)} burst {burst} latency {latency}",
        )


class NetemQdisc:
    def __init__(self, impairment: NetworkImpairment):
        self.impairment: NetworkImpairment | None = impairment

    def command(
        self,
        interface_name: str,
        parent: str,
        device_id: str,
        handle_override: str | None = None,
    ) -> Tuple[str, ...]:
        assert self.impairment is not None
        netem_args = netem_arguments(self.impairment)
        if not netem_args:
            raise TCSetupError("Netem qdisc requires delay or loss impairment.")
        args = " ".join(netem_args)
        try:
            handle = handle_override if handle_override else HandleManager.handle(device_id)
        except KeyError:
            raise TCSetupError(f"No handle available for {device_id}.")
        return (
            f"tc qdisc add dev {interface_name} parent {parent} handle {handle} "
            f"netem {args}",
        )


class TBFNetemDuoQdisc:
    def __init__(self, impairment: NetworkImpairment):
        self.impairment: NetworkImpairment | None = impairment
        self._tbf = TBFQdisc(self.impairment)
        self._netem = NetemQdisc(self.impairment)

    def command(self, interface_name: str, parent: str, device_id: str) -> Tuple[str, ...]:
        tbf_cmnds = self._tbf.command(interface_name, parent, device_id)
        netem_handle = f"{HandleManager.class_minor(device_id)}:1"
        netem_cmnds = self._netem.command(
            interface_name,
            HandleManager.handle(device_id),
            device_id,
            handle_override=netem_handle,
        )

        return tbf_cmnds + netem_cmnds


class PFiFoQdisc:
    def __init__(self, _: NetworkImpairment | None = None):
        self.impairment: NetworkImpairment | None = None

    def command(self, interface_name: str, parent: str, device_id: str) -> Tuple[str, ...]:
        try:
            handle = HandleManager.handle(device_id)
        except KeyError:
            raise TCSetupError(f"No handle available for {device_id}.")
        return (
            f"tc qdisc add dev {interface_name} parent {parent} handle {handle} "
            f"pfifo limit 10000",  # limit <number> of messages queue can hold
        )


class HTBClass:

    def __init__(self, interface: str, qdisc: LeafQDisc, device: DiscoveredDevice):
        self._interface = interface
        self.qdisc = qdisc
        self.device = device
        self.classid = HandleManager.classid(device.device_id)

    def initialise(self) -> Tuple[str, ...]:
        # htb rate which is set to high limit as HTB is not used for rate limiting
        # hardcoding per-client classes to root htb class which will always have handle as 1:1
        cmd = (
            f"tc class add dev {self._interface} parent 1:1 classid {self.classid} htb rate 1000mbit",
        )
        child_cmd = self.activate_child()
        # by adding /32 variant, we specify that all packets from root (1:) which match dst ip, should be sent to classid
        filter = (
            f"tc filter add dev {self._interface} parent 1: protocol ip prio 1 u32 match ip dst {self.device.ip_address}/32 flowid {self.classid}",
        )
        return cmd + child_cmd + filter

    def activate_child(self) -> Tuple[str, ...]:
        return self.qdisc.command(self._interface, self.classid, self.device.device_id)


def allocate_qdisc(impairment: NetworkImpairment | None) -> LeafQDisc:
    if impairment is None or (
        impairment.delay is None
        and impairment.loss is None
        and impairment.bandwidth_limit is None
    ):
        return PFiFoQdisc(impairment)
    elif impairment.bandwidth_limit is None:
        return NetemQdisc(impairment)
    elif impairment.delay or impairment.loss:
        return TBFNetemDuoQdisc(impairment)
    else:
        return TBFQdisc(impairment)


class HTBTree:
    def __init__(self, interface: str) -> None:
        self._interface = interface
        self._buffer: list[str] = [
            f"tc qdisc add dev {interface} root handle 1: htb default 99",
            f"tc class add dev {interface} parent 1: classid 1:1 htb rate 1000mbit",
            f"tc class add dev {interface} parent 1:1 classid 1:99 htb rate 1000mbit",
            f"tc qdisc add dev {interface} parent 1:99 handle 99: pfifo limit 10000",
        ]
        self._nodes: dict[str, HTBClass] = {}

    def __iter__(self) -> Iterator[HTBClass]:
        return iter(self._nodes.values())

    def pending_commands(self) -> tuple[str, ...]:
        return tuple(self._buffer)

    def clear_pending_commands(self) -> None:
        self._buffer.clear()

    def drain_pending_commands(self) -> tuple[str, ...]:
        commands = tuple(self._buffer)
        self._buffer.clear()
        return commands

    def add_node(self, device: DiscoveredDevice, qdisc: LeafQDisc) -> None:
        try:
            class_id = HandleManager.classid(device_id=device.device_id)
        except KeyError:
            logger.error(f"Can't find handle for {device.device_id}.")
            return None
        if class_id in self._nodes:
            raise TCSetupError(f"Class already exists for device {device.device_id}.")
        self._nodes[class_id] = HTBClass(interface=self._interface, qdisc=qdisc, device=device)
        self._buffer.extend(self._nodes[class_id].initialise())
