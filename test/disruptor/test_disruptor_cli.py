"""Disruptor CLI contract tests."""

from collections.abc import Callable
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from test_farm.cli import app as farm_app
from test_farm.disruptor.cli import app
from test_farm.disruptor.models import DiscoveredDevice, TCExecutionError


def test_disruptor_dry_run_resolves_discovered_devices_to_default_impairment(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
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
    monkeypatch.setattr(
        "test_farm.disruptor.cli.discover_aware_devices",
        lambda: tuple(discovered_devices(2)),
    )
    monkeypatch.setattr(
        "test_farm.disruptor.cli.apply_disruptor_tc_plan",
        lambda plan: (_ for _ in ()).throw(AssertionError(f"unexpected apply: {plan}")),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [str(scenario_file), "--interface", "wlan0", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "Disruptor dry-run plan for interface wlan0" in result.stdout
    assert "aware-10 192.0.2.10 -> default" in result.stdout
    assert "aware-11 192.0.2.11 -> default" in result.stdout
    assert (
        "tc filter add dev wlan0 parent 1: protocol ip prio 1 u32 match ip dst "
        "192.0.2.10/32 flowid 1:10"
    ) in result.stdout
    assert (
        "tc filter add dev wlan0 parent 1: protocol ip prio 1 u32 match ip dst "
        "192.0.2.11/32 flowid 1:20"
    ) in result.stdout
    assert "netem delay 100ms loss 5%" in result.stdout
    assert "tbf rate 1mbit" in result.stdout
    assert result.stderr == ""


def test_disruptor_dry_run_integrates_ordered_overrides_with_fake_discovery(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
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
            "    - name: control\n"
            "      device_match:\n"
            "        - sc-aware-11\n"
            "      impairment: none\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "test_farm.disruptor.cli.discover_aware_devices",
        lambda: tuple(discovered_devices(3)),
    )
    monkeypatch.setattr(
        "test_farm.disruptor.cli.apply_disruptor_tc_plan",
        lambda plan: (_ for _ in ()).throw(AssertionError(f"unexpected apply: {plan}")),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [str(scenario_file), "--interface", "wlan0", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "Disruptor dry-run plan for interface wlan0" in result.stdout
    assert "sc-aware-10 192.0.2.10 -> first-match" in result.stdout
    assert "sc-aware-11 192.0.2.11 -> control" in result.stdout
    assert "sc-aware-12 192.0.2.12 -> default" in result.stdout
    assert "later-match" not in result.stdout
    assert "tc qdisc add dev wlan0 parent 1:10 handle 10: netem loss 25%" in result.stdout
    assert "tc qdisc add dev wlan0 parent 1:20 handle 20: pfifo limit 10000" in result.stdout
    assert "tc qdisc add dev wlan0 parent 1:30 handle 30: netem delay 100ms" in result.stdout
    assert result.stderr == ""


def test_disruptor_non_dry_run_applies_plan_for_explicit_interface(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        ("network_impairment:\n" "  default:\n" "    delay: 100ms\n"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "test_farm.disruptor.cli.discover_aware_devices",
        lambda: tuple(discovered_devices(1)),
    )
    applied_interfaces: list[str] = []
    monkeypatch.setattr(
        "test_farm.disruptor.cli.apply_disruptor_tc_plan",
        lambda plan: applied_interfaces.append(plan.interface_name),
    )
    runner = CliRunner()

    result = runner.invoke(app, [str(scenario_file), "--interface", "wlan0"])

    assert result.exit_code == 0
    assert applied_interfaces == ["wlan0"]
    assert "Disruptor starting network impairment" in result.stderr


def test_disruptor_non_dry_run_reports_tc_capability_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    discovered_devices: Callable[[int], list[DiscoveredDevice]],
) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        ("network_impairment:\n" "  default:\n" "    delay: 100ms\n"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "test_farm.disruptor.cli.discover_aware_devices",
        lambda: tuple(discovered_devices(1)),
    )
    monkeypatch.setattr(
        "test_farm.disruptor.cli.apply_disruptor_tc_plan",
        lambda plan: (_ for _ in ()).throw(
            TCExecutionError(
                "RTNETLINK answers: Operation not permitted\n"
                "Disruptor requires CAP_NET_ADMIN to modify tc state."
            )
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, [str(scenario_file), "--interface", "wlan0"])

    assert result.exit_code == 1
    assert "Operation not permitted" in result.stderr
    assert "CAP_NET_ADMIN" in result.stderr


def test_disruptor_requires_interface(tmp_path: Path) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    scenario_file.write_text(
        ("network_impairment:\n" "  default:\n" "    delay: 100ms\n"),
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(app, [str(scenario_file), "--dry-run"])

    assert result.exit_code == 2
    assert "--interface" in result.stderr


def test_disruptor_throws_if_scenario_fails_to_load(tmp_path: Path) -> None:
    scenario_file = tmp_path / "disruptor.yaml"
    runner = CliRunner()

    result = runner.invoke(app, [str(scenario_file), "--dry-run", "--interface", "skynet"])

    assert result.exit_code == 2


def test_test_farm_cli_does_not_expose_disrupt_subcommand() -> None:
    runner = CliRunner()

    result = runner.invoke(farm_app, ["disrupt"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr
