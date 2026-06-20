from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from test_farm.disruptor.device_tree import HTBTree

from test_farm import scenario
from test_farm.network_impairment import NetworkImpairment
from test_farm.scenario import DisruptorScenario, Selector


class TCSetupError(Exception):
    """Raised when encountering some error while setting up TC commands"""


@dataclass(frozen=True)
class DiscoveredDevice:
    """A real Slamcore Aware device discovered by the Disruptor."""

    device_id: str
    ip_address: str


@dataclass(frozen=True)
class DisruptorTcDevicePlan:
    """A device-specific resolved tc plan lane."""

    device: DiscoveredDevice
    policy_name: str
    impairment: NetworkImpairment | None
    class_id: str
    commands: tuple[str, ...]


@dataclass(frozen=True)
class DisruptorTcPlan:
    """A typed tc plan for one dry-run Disruptor invocation."""

    interface_name: str
    routing_tree: "HTBTree"
    scenario: DisruptorScenario
    warnings: tuple["DisruptorResolverWarning", ...] = ()


@dataclass(frozen=True)
class DisruptorResolverWarning:
    """A structured policy-resolution warning."""

    policy_name: str
    selector: Selector
