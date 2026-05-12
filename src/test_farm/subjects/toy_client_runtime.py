"""Runtime entrypoint for the containerized toy client."""

import asyncio

from test_farm.subjects.toy_client import run_toy_client


def main() -> None:
    """Run the toy client using process environment variables."""

    raise SystemExit(asyncio.run(run_toy_client()))


if __name__ == "__main__":
    main()
