"""Network impairment models and tc command rendering."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NetworkImpairment:
    """The supported static Router Container impairment subset."""

    delay: str | None = None
    loss: float | None = None
    bandwidth_limit: str | None = None


def router_tc_commands(
    *,
    network_impairment: NetworkImpairment,
    interface_name: str,
) -> tuple[str, ...]:
    """Render tc commands for the supported Router Container impairment subset."""

    netem_arguments: list[str] = []
    if network_impairment.delay is not None:
        netem_arguments.extend(["delay", network_impairment.delay])
    if network_impairment.loss is not None:
        netem_arguments.extend(["loss", _format_loss(network_impairment.loss)])

    if network_impairment.bandwidth_limit is None:
        return (f"tc qdisc add dev {interface_name} root netem {' '.join(netem_arguments)}",)

    commands = [
        (
            f"tc qdisc add dev {interface_name} root handle 1: "
            f"tbf rate {network_impairment.bandwidth_limit}"
        )
    ]
    if netem_arguments:
        commands.append(
            (
                f"tc qdisc add dev {interface_name} parent 1:1 handle 10: "
                f"netem {' '.join(netem_arguments)}"
            )
        )

    return tuple(commands)


def _format_loss(loss: float) -> str:
    if loss.is_integer():
        return f"{int(loss)}%"
    return f"{loss}%"
