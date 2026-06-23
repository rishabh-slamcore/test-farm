from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from test_farm.disruptor.device_tree import HTBTree

from test_farm.models import DiscoveredDevice
from test_farm.network_impairment import NetworkImpairment
from test_farm.scenario import DisruptorScenario, Selector


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
    scenario: DisruptorScenario
    warnings: tuple["ResolverWarning", ...] = ()


@dataclass(frozen=True)
class ResolverWarning:
    """A structured policy-resolution warning."""

    policy_name: str
    selector: Selector
