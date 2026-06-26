import logging
from typing import ClassVar, Iterator, Protocol, Sequence, Tuple

from disruptor.impairment import (
    _format_bandwidth_limit,
    compute_burst,
    netem_arguments,
    read_mtu,
    validate_burst,
)
from disruptor.models import DiscoveredDevice, NetworkImpairment, TCSetupError

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
            # arbritary scheme so that handles don't clash
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

    @classmethod
    def add_default(cls) -> None:
        cls._handles["default"] = 10

    @classmethod
    def default_handle(cls) -> str:
        return f"{cls.class_minor("default")}:"


class LeafQDisc(Protocol):
    impairment: NetworkImpairment | None

    def command(self, interface_name: str, parent: str, handle: str) -> Tuple[str, ...]: ...


class TBFQdisc:
    def __init__(self, impairment: NetworkImpairment):
        self.impairment: NetworkImpairment | None = impairment

    def command(self, interface_name: str, parent: str, handle: str) -> Tuple[str, ...]:
        assert self.impairment is not None
        bps = self.impairment.bandwidth_limit
        assert bps is not None
        mtu = read_mtu(interface_name)
        burst = compute_burst(bps, mtu)
        validate_burst(burst, mtu, bps)
        latency = "50ms"  # packets waiting for more than latency will be dropped from queue
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
        handle: str,
    ) -> Tuple[str, ...]:
        assert self.impairment is not None
        netem_args = netem_arguments(self.impairment)
        if not netem_args:
            raise TCSetupError("Netem qdisc requires delay or loss impairment.")
        args = " ".join(netem_args)
        return (
            f"tc qdisc add dev {interface_name} parent {parent} handle {handle} "
            f"netem {args}",
        )


class TBFNetemDuoQdisc:
    def __init__(self, impairment: NetworkImpairment):
        self.impairment: NetworkImpairment | None = impairment
        self._tbf = TBFQdisc(self.impairment)
        self._netem = NetemQdisc(self.impairment)

    def command(self, interface_name: str, parent: str, handle: str) -> Tuple[str, ...]:
        tbf_cmnds = self._tbf.command(interface_name, parent, handle)
        netem_handle = handle + "1"
        netem_cmnds = self._netem.command(
            interface_name=interface_name,
            parent=handle,
            handle=netem_handle,
        )

        return tbf_cmnds + netem_cmnds


class PFiFoQdisc:
    def __init__(self, _: NetworkImpairment | None = None):
        self.impairment: NetworkImpairment | None = None

    def command(self, interface_name: str, parent: str, handle: str) -> Tuple[str, ...]:
        return (
            f"tc qdisc add dev {interface_name} parent {parent} handle {handle} "
            f"pfifo limit 10000",  # limit <number> of messages queue can hold
        )


class HTBClass:

    def __init__(
        self, interface: str, qdisc: LeafQDisc, device: DiscoveredDevice | None = None
    ):
        self._interface = interface
        self.qdisc = qdisc
        self.device = device

    @classmethod
    def setup_class(cls, interface: str, classid: str) -> tuple[str, ...]:
        # htb rate which is set to high limit as HTB is not used for rate limiting
        # hardcoding per-client classes to root htb class which will always have handle as 1:1
        return (
            f"tc class add dev {interface} parent 1:1 classid {classid} htb rate 1000mbit",
        )

    @classmethod
    def setup_filter(cls, interface: str, classid: str, ip_address: str) -> tuple[str, ...]:
        # by adding /32 variant, we specify that all packets from root (1:) which match dst ip, should be sent to classid
        return (
            f"tc filter add dev {interface} parent 1: protocol ip prio 1 u32 match ip dst {ip_address}/32 flowid {classid}",
        )

    @classmethod
    def get_classid(cls, device: DiscoveredDevice | None) -> str:
        if device is None:
            raise TCSetupError("Node incorrectly setup. No device available")

        device_id = device.device_id

        try:
            return HandleManager.classid(device_id)
        except KeyError:
            raise TCSetupError(f"No handle available for {device_id}.")

    def initialise(self) -> Tuple[str, ...]:
        assert self.device is not None
        classid = HTBClass.get_classid(device=self.device)
        class_setup_cmd = HTBClass.setup_class(self._interface, classid)
        stateless_qdisc_setup = self.activate_node(self.device.device_id)
        filter = HTBClass.setup_filter(
            interface=self._interface, classid=classid, ip_address=self.device.ip_address
        )
        return class_setup_cmd + stateless_qdisc_setup + filter

    def activate_node(self, device_id: str) -> Tuple[str, ...]:
        try:
            handle = HandleManager.handle(device_id)
            classid = HTBClass.get_classid(device=self.device)
        except KeyError:
            raise TCSetupError(f"No handle available for {device_id}.")

        return self.qdisc.command(self._interface, classid, handle)


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
        self._default_node_added = False

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

    def add_node(self, qdisc: LeafQDisc, device: DiscoveredDevice) -> None:
        try:
            class_id = HandleManager.classid(device_id=device.device_id)
        except KeyError:
            logger.error(f"Can't find handle for {device.device_id}.")
            return None
        if class_id in self._nodes:
            raise TCSetupError(f"Class already exists for device {device.device_id}.")
        self._nodes[class_id] = HTBClass(interface=self._interface, qdisc=qdisc, device=device)
        self._buffer.extend(self._nodes[class_id].initialise())

    def add_default(self, qdisc: LeafQDisc) -> None:
        if self._default_node_added:
            raise TCSetupError(f"Default impairment already setup")
        HandleManager.add_default()
        class_id = HandleManager.classid(device_id="default")
        node = HTBClass(interface=self._interface, qdisc=qdisc)
        self._nodes[class_id] = node

        commands = HTBClass.setup_class(
            interface=self._interface, classid=class_id
        ) + qdisc.command(
            interface_name=self._interface,
            parent=class_id,
            handle=HandleManager.default_handle(),
        )
        self._buffer.extend(commands)
        self._default_node_added = True
