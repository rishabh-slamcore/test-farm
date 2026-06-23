"""Disruptor tc device tree tests."""

from collections.abc import Callable

import pytest
from pytest import MonkeyPatch

from test_farm.disruptor.device_tree import (
    HandleManager,
    HTBClass,
    HTBTree,
    NetemQdisc,
    PFiFoQdisc,
    TBFNetemDuoQdisc,
    allocate_qdisc,
)
from test_farm.disruptor.models import TCSetupError
from test_farm.models import DiscoveredDevice
from test_farm.network_impairment import NetworkImpairment


def test_htb_tree_exposes_pending_commands_without_clearing_them() -> None:
    tree = HTBTree("wlan0")

    commands = tree.pending_commands()

    assert commands == (
        "tc qdisc add dev wlan0 root handle 1: htb default 99",
        "tc class add dev wlan0 parent 1: classid 1:1 htb rate 1000mbit",
        "tc class add dev wlan0 parent 1:1 classid 1:99 htb rate 1000mbit",
        "tc qdisc add dev wlan0 parent 1:99 handle 99: pfifo limit 10000",
    )
    assert tree.pending_commands() == commands


def test_htb_tree_drains_pending_commands() -> None:
    tree = HTBTree("wlan0")

    commands = tree.drain_pending_commands()

    assert commands == (
        "tc qdisc add dev wlan0 root handle 1: htb default 99",
        "tc class add dev wlan0 parent 1: classid 1:1 htb rate 1000mbit",
        "tc class add dev wlan0 parent 1:1 classid 1:99 htb rate 1000mbit",
        "tc qdisc add dev wlan0 parent 1:99 handle 99: pfifo limit 10000",
    )
    assert tree.pending_commands() == ()


def test_htb_tree_clears_pending_commands() -> None:
    tree = HTBTree("wlan0")

    tree.clear_pending_commands()

    assert tree.pending_commands() == ()


def test_handle_manager_rejects_duplicate_device_ids() -> None:
    HandleManager.clear()
    devices = [
        DiscoveredDevice(device_id="sc-aware-10", ip_address="192.0.2.10", variant="mk3a"),
        DiscoveredDevice(device_id="sc-aware-10", ip_address="192.0.2.11", variant="mk3a"),
    ]

    with pytest.raises(TCSetupError, match="Duplicate device id discovered: sc-aware-10"):
        HandleManager.setup(devices)


def test_bandwidth_and_netem_impairment_renders_tbf_then_child_netem(
    monkeypatch: MonkeyPatch,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    monkeypatch.setattr("test_farm.disruptor.device_tree.read_mtu", lambda interface: 1500)
    devices = discovered_devices(2)
    impairment = NetworkImpairment(delay=0.1, loss=5.0, bandwidth_limit=1_000_000)
    qdisc = allocate_qdisc(impairment)

    assert isinstance(qdisc, TBFNetemDuoQdisc)
    for index, device in enumerate(devices, start=1):
        class_minor = index * 10
        assert qdisc.command("wlan0", f"1:{class_minor}", f"{class_minor}:") == (
            f"tc qdisc add dev wlan0 parent 1:{class_minor} handle {class_minor}: "
            "tbf rate 1mbit burst 6000 latency 50ms",
            f"tc qdisc add dev wlan0 parent {class_minor}: "
            f"handle {class_minor}:1 netem delay 100ms loss 5%",
        )


def test_htb_tree_adds_per_device_class_qdisc_and_filter(
    monkeypatch: MonkeyPatch,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    monkeypatch.setattr("test_farm.disruptor.device_tree.read_mtu", lambda interface: 1500)
    devices = discovered_devices(20)
    tree = HTBTree("wlan0")

    for device in devices:
        tree.add_node(
            qdisc=allocate_qdisc(NetworkImpairment(bandwidth_limit=1_000_000)),
            device=device,
        )

    per_device_commands = tree.pending_commands()[4:]
    assert len(per_device_commands) == len(devices) * 3
    for index, device in enumerate(devices, start=1):
        class_minor = index * 10
        class_id = f"1:{class_minor}"
        qdisc_handle = f"{class_minor}:"
        command_index = (index - 1) * 3
        assert per_device_commands[command_index] == (
            f"tc class add dev wlan0 parent 1:1 classid {class_id} htb rate 1000mbit"
        )
        assert per_device_commands[command_index + 1] == (
            f"tc qdisc add dev wlan0 parent {class_id} handle {qdisc_handle} "
            "tbf rate 1mbit burst 6000 latency 50ms"
        )
        assert per_device_commands[command_index + 2] == (
            "tc filter add dev wlan0 parent 1: protocol ip prio 1 u32 "
            f"match ip dst {device.ip_address}/32 flowid {class_id}"
        )
        assert (
            sum(f"handle {qdisc_handle} " in command for command in per_device_commands) == 1
        )


def test_htb_tree_adds_default_impairment_node_without_device() -> None:
    HandleManager.clear()
    tree = HTBTree("wlan0")

    tree.add_default(qdisc=allocate_qdisc(NetworkImpairment(delay=0.1)))

    nodes = list(tree)
    commands = tree.pending_commands()
    assert len(nodes) == 1
    assert nodes[0].device is None
    assert nodes[0].qdisc.impairment == NetworkImpairment(delay=0.1)
    assert "tc class add dev wlan0 parent 1:1 classid 1:10 htb rate 1000mbit" in commands
    assert "tc qdisc add dev wlan0 parent 1:10 handle 10: netem delay 100ms" in commands
    assert not any(command.startswith("tc filter add") for command in commands)


def test_htb_tree_rejects_adding_default_impairment_twice() -> None:
    HandleManager.clear()
    tree = HTBTree("wlan0")
    tree.add_default(qdisc=allocate_qdisc(NetworkImpairment(delay=0.1)))
    pending_commands = tree.pending_commands()

    with pytest.raises(TCSetupError, match="Default impairment already setup"):
        tree.add_default(qdisc=allocate_qdisc(NetworkImpairment(delay=0.2)))

    assert tree.pending_commands() == pending_commands


def test_htb_tree_default_impairment_renders_bandwidth_then_child_netem(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("test_farm.disruptor.device_tree.read_mtu", lambda interface: 1500)
    HandleManager.clear()
    tree = HTBTree("wlan0")

    tree.add_default(
        qdisc=allocate_qdisc(NetworkImpairment(delay=0.1, loss=5.0, bandwidth_limit=1_000_000))
    )

    assert tree.pending_commands()[-3:] == (
        "tc class add dev wlan0 parent 1:1 classid 1:10 htb rate 1000mbit",
        "tc qdisc add dev wlan0 parent 1:10 handle 10: "
        "tbf rate 1mbit burst 6000 latency 50ms",
        "tc qdisc add dev wlan0 parent 10: handle 10:1 netem delay 100ms loss 5%",
    )


def test_htb_tree_rejects_adding_the_same_device_twice(
    monkeypatch: MonkeyPatch,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    monkeypatch.setattr("test_farm.disruptor.device_tree.read_mtu", lambda interface: 1500)
    device = discovered_devices(1)[0]
    tree = HTBTree("wlan0")
    tree.add_node(
        qdisc=allocate_qdisc(NetworkImpairment(bandwidth_limit=1_000_000)), device=device
    )
    pending_commands = tree.pending_commands()

    with pytest.raises(TCSetupError, match="Class already exists for device sc-aware-10"):
        tree.add_node(
            qdisc=allocate_qdisc(NetworkImpairment(bandwidth_limit=1_000_000)), device=device
        )

    assert tree.pending_commands() == pending_commands


def test_htb_tree_does_not_add_node_for_device_without_handle() -> None:
    HandleManager.clear()
    tree = HTBTree("wlan0")
    initial_commands = tree.pending_commands()
    unknown_device = DiscoveredDevice(
        device_id="sc-aware-missing",
        ip_address="192.0.2.200",
        variant="mk3a",
    )

    tree.add_node(qdisc=allocate_qdisc(None), device=unknown_device)

    assert list(tree) == []
    assert tree.pending_commands() == initial_commands


def test_empty_impairment_allocates_unimpaired_pfifo_qdisc() -> None:
    qdisc = allocate_qdisc(NetworkImpairment())

    assert isinstance(qdisc, PFiFoQdisc)
    assert qdisc.impairment is None


def test_netem_qdisc_rejects_empty_impairment(
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    device = discovered_devices(1)[0]
    qdisc = NetemQdisc(NetworkImpairment())

    with pytest.raises(TCSetupError, match="Netem qdisc requires delay or loss impairment"):
        qdisc.command("wlan0", "1:10", device.device_id)


@pytest.mark.parametrize(
    "impairment",
    [
        None,
        NetworkImpairment(delay=0.1),
        NetworkImpairment(bandwidth_limit=1_000_000),
        NetworkImpairment(delay=0.1, loss=5.0, bandwidth_limit=1_000_000),
    ],
)
def test_leaf_qdisc_command_raises_for_device_without_handle(
    monkeypatch: MonkeyPatch,
    impairment: NetworkImpairment | None,
) -> None:
    monkeypatch.setattr("test_farm.disruptor.device_tree.read_mtu", lambda interface: 1500)
    HandleManager.clear()
    qdisc = allocate_qdisc(impairment)

    with pytest.raises(TCSetupError, match="No handle available for sc-aware-missing"):
        qdisc.command("wlan0", "1:10", "sc-aware-missing")
