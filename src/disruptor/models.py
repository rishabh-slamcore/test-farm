"""Disruptor domain and tc planning models."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from disruptor.device_tree import HTBTree
    from disruptor.scenario import DisruptorScenario, Selector

DEVICE_VARIANTS: tuple[str, ...] = ("mk2", "mk3a", "mk3b", "mk3c")


@dataclass(frozen=True)
class DiscoveredDevice:
    """A real Slamcore Aware device discovered by the Disruptor."""

    device_id: str
    ip_address: str
    variant: str


@dataclass(frozen=True)
class NetworkImpairment:
    """The supported static Disruptor impairment subset."""

    delay: float | None = None
    loss: float | None = None
    bandwidth_limit: int | None = None


class TCSetupError(Exception):
    """Raised when encountering some error while setting up TC commands"""


class TCExecutionError(Exception):
    """Raised when applying Disruptor tc commands fails."""


@dataclass(frozen=True)
class TCDevicePlan:
    """A device-specific resolved tc plan lane."""

    device: DiscoveredDevice
    policy_name: str
    impairment: NetworkImpairment | None
    class_id: str
    commands: tuple[str, ...]


@dataclass(frozen=True)
class TCPlan:
    """A typed tc plan for one dry-run Disruptor invocation."""

    interface_name: str
    routing_tree: "HTBTree"
    scenario: "DisruptorScenario"
    warnings: tuple["ResolverWarning", ...] = ()


@dataclass(frozen=True)
class ResolverWarning:
    """A structured policy-resolution warning."""

    policy_name: str
    selector: "Selector"
