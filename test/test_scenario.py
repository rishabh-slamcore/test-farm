"""Scenario parsing tests."""

from pathlib import Path

import pytest

from test_farm.scenario import ScenarioFileError, load_scenario_file


@pytest.mark.parametrize(
    (
        "network_impairment_lines",
        "expected_delay",
        "expected_loss",
        "expected_bandwidth_limit",
    ),
    [
        (["delay: 100ms"], 0.1, None, None),
        (["delay: 250us"], 0.00025, None, None),
        (["delay: 1.5s"], 1.5, None, None),
        (["loss: 5"], None, 5.0, None),
        (["loss: 12.5%"], None, 12.5, None),
        (["bandwidth_limit: 1mbit"], None, None, 1_000_000),
        (["bandwidth_limit: 1.5kbit"], None, None, 1_500),
        (
            [
                "delay: 100ms",
                "loss: 5%",
                "bandwidth_limit: 1mbit",
            ],
            0.1,
            5.0,
            1_000_000,
        ),
    ],
)
def test_load_scenario_file_parses_network_impairment_fields(
    tmp_path: Path,
    network_impairment_lines: list[str],
    expected_delay: float | None,
    expected_loss: float | None,
    expected_bandwidth_limit: int | None,
) -> None:
    scenario_file = tmp_path / "impaired.yaml"
    network_impairment_body = "\n".join(
        f"  {network_impairment_line}" for network_impairment_line in network_impairment_lines
    )
    scenario_file.write_text(
        (
            "client_count: 1\n"
            "receipt_timeout_seconds: 2\n"
            "network_impairment:\n"
            f"{network_impairment_body}\n"
        ),
        encoding="utf-8",
    )

    scenario = load_scenario_file(scenario_file)

    assert scenario.network_impairment is not None
    assert scenario.network_impairment.delay == expected_delay
    assert scenario.network_impairment.loss == expected_loss
    assert scenario.network_impairment.bandwidth_limit == expected_bandwidth_limit


@pytest.mark.parametrize(
    ("network_impairment_lines", "expected_error"),
    [
        ([], "must set at least one network_impairment field"),
        (["delay: 100"], "must set network_impairment.delay to a duration like 100ms"),
        (["delay: true"], "must set network_impairment.delay to a duration like 100ms"),
        (
            ["delay: 100msec"],
            "must set network_impairment.delay to a duration like 100ms",
        ),
        (
            ["delay: 10milliseconds"],
            "must set network_impairment.delay to a duration like 100ms",
        ),
        (
            ["delay: 5sec"],
            "must set network_impairment.delay to a duration like 100ms",
        ),
        (
            ["loss: -1"],
            "must set network_impairment.loss to a percentage between 0 and 100",
        ),
        (
            ["loss: 101"],
            "must set network_impairment.loss to a percentage between 0 and 100",
        ),
        (
            ["loss: high"],
            "must set network_impairment.loss to a percentage between 0 and 100",
        ),
        (
            ["bandwidth_limit: 0bit"],
            "must set network_impairment.bandwidth_limit to a rate like 1mbit",
        ),
        (
            ["bandwidth_limit: 1.1bit"],
            "must set network_impairment.bandwidth_limit to a rate like 1mbit",
        ),
        (
            ["bandwidth_limit: 1mbps"],
            "must set network_impairment.bandwidth_limit to a rate like 1mbit",
        ),
        (
            ["bandwidth_limit: 10kbps"],
            "must set network_impairment.bandwidth_limit to a rate like 1mbit",
        ),
        (
            ["bandwidth_limit: 2megabit"],
            "must set network_impairment.bandwidth_limit to a rate like 1mbit",
        ),
        (
            ["bandwidth_limit: fast"],
            "must set network_impairment.bandwidth_limit to a rate like 1mbit",
        ),
        (
            ["jitter: 20ms"],
            "contains unknown network_impairment fields: jitter",
        ),
        (
            ["dely: 100ms"],
            "contains unknown network_impairment fields: dely",
        ),
        (
            ["los: 5%"],
            "contains unknown network_impairment fields: los",
        ),
        (
            ["bandwith_limit: 1mbit"],
            "contains unknown network_impairment fields: bandwith_limit",
        ),
        (
            [
                "delaay: 100ms",
                "bandwidth_lmit: 1mbit",
            ],
            "contains unknown network_impairment fields: bandwidth_lmit, delaay",
        ),
    ],
)
def test_load_scenario_file_rejects_invalid_network_impairment_fields(
    tmp_path: Path,
    network_impairment_lines: list[str],
    expected_error: str,
) -> None:
    scenario_file = tmp_path / "invalid.yaml"
    if network_impairment_lines:
        network_impairment_body = "\n".join(
            f"  {network_impairment_line}"
            for network_impairment_line in network_impairment_lines
        )
        scenario_contents = (
            "client_count: 1\n"
            "receipt_timeout_seconds: 2\n"
            "network_impairment:\n"
            f"{network_impairment_body}\n"
        )
    else:
        scenario_contents = (
            "client_count: 1\n" "receipt_timeout_seconds: 2\n" "network_impairment: {}\n"
        )

    scenario_file.write_text(scenario_contents, encoding="utf-8")

    with pytest.raises(ScenarioFileError, match=expected_error):
        load_scenario_file(scenario_file)
