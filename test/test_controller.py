"""Controller receipt-channel tests."""

from fastapi.testclient import TestClient

from test_farm.controller import ControllerState, create_controller_app
from test_farm.models import DEFAULT_BUNDLE


def test_controller_reports_health_and_accepts_one_valid_receipt() -> None:
    state = ControllerState(
        invocation_instance=1,
        client_count=1,
        expected_bundle=DEFAULT_BUNDLE,
    )
    with TestClient(create_controller_app(state)) as client:
        health = client.get("/health")
        receipt = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=DEFAULT_BUNDLE.to_payload(),
        )
        late_receipt = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=DEFAULT_BUNDLE.to_payload(),
        )

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert receipt.status_code == 202
    assert receipt.json() == {"status": "accepted"}
    assert late_receipt.status_code == 410
    assert late_receipt.json() == {
        "status": "rejected",
        "detail": "Receipt channel is closed for this invocation.",
    }


def test_controller_rejects_mismatched_receipt_without_marking_success() -> None:
    state = ControllerState(
        invocation_instance=1,
        client_count=1,
        expected_bundle=DEFAULT_BUNDLE,
    )
    with TestClient(create_controller_app(state)) as client:
        response = client.post(
            "/invocations/1/clients/client-001/receipt",
            json={
                "bundle_id": DEFAULT_BUNDLE.bundle_id,
                "byte_count": DEFAULT_BUNDLE.byte_count,
                "checksum": "not-the-expected-checksum",
            },
        )
        valid_receipt = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=DEFAULT_BUNDLE.to_payload(),
        )

    assert response.status_code == 409
    assert response.json() == {
        "status": "rejected",
        "detail": "Receipt checksum did not match the expected bundle.",
    }
    assert valid_receipt.status_code == 202
    assert valid_receipt.json() == {"status": "accepted"}


def test_controller_rejects_late_receipt_after_timeout() -> None:
    state = ControllerState(
        invocation_instance=1,
        client_count=1,
        expected_bundle=DEFAULT_BUNDLE,
    )
    state._receipt_channel_open = False

    with TestClient(create_controller_app(state)) as client:
        response = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=DEFAULT_BUNDLE.to_payload(),
        )

    assert response.status_code == 410
    assert response.json() == {
        "status": "rejected",
        "detail": "Receipt channel is closed for this invocation.",
    }


def test_controller_keeps_receipt_channel_open_until_every_expected_client_reports() -> None:
    state = ControllerState(
        invocation_instance=1,
        client_count=2,
        expected_bundle=DEFAULT_BUNDLE,
    )

    with TestClient(create_controller_app(state)) as client:
        first_receipt = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=DEFAULT_BUNDLE.to_payload(),
        )
        second_receipt = client.post(
            "/invocations/1/clients/client-002/receipt",
            json=DEFAULT_BUNDLE.to_payload(),
        )
        late_receipt = client.post(
            "/invocations/1/clients/client-002/receipt",
            json=DEFAULT_BUNDLE.to_payload(),
        )

    assert first_receipt.status_code == 202
    assert second_receipt.status_code == 202
    assert late_receipt.status_code == 410
    assert state.expected_client_ids == ("client-001", "client-002")
    assert state.accepted_client_ids == frozenset({"client-001", "client-002"})
