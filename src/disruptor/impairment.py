"""Network impairment models and tc command rendering helpers for Disruptor."""

from contextlib import suppress

from disruptor.models import NetworkImpairment

_DELAY_UNITS = (
    ("s", 1_000_000),
    ("ms", 1_000),
    ("us", 1),
)
_BANDWIDTH_UNITS = (
    ("tbit", 1_000_000_000_000),
    ("gbit", 1_000_000_000),
    ("mbit", 1_000_000),
    ("kbit", 1_000),
)


def read_mtu(nic: str) -> int:
    mtu = 1500
    with suppress(Exception):
        with open(f"/sys/class/net/{nic}/mtu") as file:
            mtu = int(file.read())
    return mtu


def compute_burst(rate_bps: int, mtu: int) -> int:
    """Compute a sane TBF burst in bytes."""
    target_duration_ms = 1
    duration_based = rate_bps * target_duration_ms // 8000
    mtu_floor = mtu * 4
    return max(duration_based, mtu_floor)


def validate_burst(burst: int, mtu: int, rate_bps: int) -> None:
    if burst < mtu:
        raise ValueError(f"burst {burst} below MTU {mtu}")
    free_pass_seconds = burst * 8 / rate_bps
    if free_pass_seconds > 1.0:
        raise ValueError(
            f"burst {burst} gives {free_pass_seconds:.1f}s free pass at rate {rate_bps} - "
            f"likely a misconfiguration"
        )


def netem_arguments(network_impairment: NetworkImpairment) -> list[str]:
    arguments: list[str] = []
    if network_impairment.delay is not None:
        arguments.extend(["delay", _format_delay(network_impairment.delay)])
    if network_impairment.loss is not None:
        arguments.extend(["loss", _format_loss(network_impairment.loss)])
    return arguments


def _format_loss(loss: float) -> str:
    if loss.is_integer():
        return f"{int(loss)}%"
    return f"{loss}%"


def _format_delay(delay_seconds: float) -> str:
    for unit, unit_microseconds in _DELAY_UNITS:
        scaled_delay = delay_seconds * 1_000_000 / unit_microseconds
        rounded_scaled_delay = round(scaled_delay)
        if abs(scaled_delay - rounded_scaled_delay) < 1e-12:
            return f"{rounded_scaled_delay}{unit}"

    formatted_seconds = format(delay_seconds, ".15f").rstrip("0").rstrip(".")
    return f"{formatted_seconds}s"


def _format_bandwidth_limit(rate_bps: int) -> str:
    for unit, factor in _BANDWIDTH_UNITS:
        if rate_bps % factor == 0:
            return f"{rate_bps // factor}{unit}"

    return f"{rate_bps}bit"
