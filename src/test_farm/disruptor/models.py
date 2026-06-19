from dataclasses import dataclass

from test_farm.network_impairment import NetworkImpairment
from test_farm.scenario import Selector


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
    device_plans: tuple[DisruptorTcDevicePlan, ...]
    commands: tuple[str, ...]
    warnings: tuple["DisruptorResolverWarning", ...] = ()


@dataclass(frozen=True)
class DisruptorResolverWarning:
    """A structured policy-resolution warning."""

    policy_name: str
    selector: Selector
