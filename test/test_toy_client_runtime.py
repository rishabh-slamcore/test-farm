"""Toy-client runtime entrypoint tests."""

import pytest
from pytest import MonkeyPatch

from test_farm.subjects.toy_client_runtime import main


def test_toy_client_runtime_main_exits_with_toy_client_status(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "test_farm.subjects.toy_client_runtime.run_toy_client", lambda: object()
    )
    monkeypatch.setattr("test_farm.subjects.toy_client_runtime.asyncio.run", lambda task: 7)

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 7
