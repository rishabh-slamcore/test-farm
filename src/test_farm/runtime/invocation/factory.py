from test_farm.runtime.invocation.docker import DockerInvocationRunner
from test_farm.runtime.invocation_protocol import InvocationRunner


def create_default_invocation_runner(*, invocation_instance: int) -> InvocationRunner:
    """Create the production invocation runner."""

    return DockerInvocationRunner(invocation_instance=invocation_instance)
