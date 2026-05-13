from pathlib import Path
from subprocess import CompletedProcess
from typing import Protocol


class CommandRunner(Protocol):
    """Run one Docker command for runtime preparation."""

    def __call__(self, args: list[str], *, cwd: Path) -> CompletedProcess[str]:
        """Execute the given command in the provided working directory."""
        raise NotImplementedError
