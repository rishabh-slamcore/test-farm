from test_farm.runtime.invocation.docker import DockerInvocationRunner
from test_farm.runtime.invocation_protocol import InvocationRunner


def create_default_invocation_runner() -> InvocationRunner:
    """Create the production invocation runner."""

    return DockerInvocationRunner()
