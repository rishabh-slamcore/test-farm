"""Controller receipt-channel tests."""

from fastapi.testclient import TestClient

from test_farm.controller import ControllerState, create_controller_app
from test_farm.identifiers import expected_client_ids
from test_farm.models import DEFAULT_BUNDLE, ClientStatus


def test_controller_reports_health_and_accepts_one_valid_receipt() -> None:
    state = ControllerState(
        invocation_instance=1,
        expected_client_ids=expected_client_ids(1),
        expected_bundle=DEFAULT_BUNDLE,
    )
    with TestClient(create_controller_app(state)) as client:
        health = client.get("/health")
        receipt = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=_success_receipt_payload(),
        )
        late_receipt = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=_success_receipt_payload(),
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


def test_controller_accepts_mismatched_success_receipt_and_records_checksum_mismatch() -> None:
    state = ControllerState(
        invocation_instance=1,
        expected_client_ids=expected_client_ids(1),
        expected_bundle=DEFAULT_BUNDLE,
    )
    with TestClient(create_controller_app(state)) as client:
        response = client.post(
            "/invocations/1/clients/client-001/receipt",
            json={
                "client_status": "success",
                "reported_bundle": {
                    "bundle_id": DEFAULT_BUNDLE.bundle_id,
                    "byte_count": DEFAULT_BUNDLE.byte_count,
                    "checksum": "not-the-expected-checksum",
                },
            },
        )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert state.client_outcomes["client-001"].client_status == ClientStatus.CHECKSUM_MISMATCH
    assert (
        state.client_outcomes["client-001"].error_detail
        == "Receipt checksum did not match the expected bundle."
    )
    assert state.client_outcomes["client-001"].reported_bundle is not None


def test_controller_accepts_download_failed_receipt_and_records_client_outcome() -> None:
    state = ControllerState(
        invocation_instance=1,
        expected_client_ids=expected_client_ids(1),
        expected_bundle=DEFAULT_BUNDLE,
    )

    with TestClient(create_controller_app(state)) as client:
        response = client.post(
            "/invocations/1/clients/client-001/receipt",
            json={
                "client_status": "download_failed",
                "error_detail": "bundle download failed",
            },
        )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert state.client_outcomes["client-001"].client_status == ClientStatus.DOWNLOAD_FAILED
    assert state.client_outcomes["client-001"].bundle_id == DEFAULT_BUNDLE.bundle_id
    assert state.client_outcomes["client-001"].error_detail == "bundle download failed"


def test_controller_rejects_malformed_receipt_without_recording_client_outcome() -> None:
    state = ControllerState(
        invocation_instance=1,
        expected_client_ids=expected_client_ids(1),
        expected_bundle=DEFAULT_BUNDLE,
    )

    with TestClient(create_controller_app(state)) as client:
        response = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=DEFAULT_BUNDLE.to_payload(),
        )

    assert response.status_code == 400
    assert response.json() == {
        "status": "rejected",
        "detail": "Receipt body must be a JSON object.",
    }
    assert state.client_outcomes == {}


def test_controller_latest_receipt_wins_until_timeout_but_success_is_sticky() -> None:
    state = ControllerState(
        invocation_instance=1,
        expected_client_ids=expected_client_ids(2),
        expected_bundle=DEFAULT_BUNDLE,
    )

    with TestClient(create_controller_app(state)) as client:
        first_receipt = client.post(
            "/invocations/1/clients/client-001/receipt",
            json={
                "client_status": "download_failed",
                "error_detail": "bundle download failed",
            },
        )
        second_receipt = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=_success_receipt_payload(),
        )
        third_receipt = client.post(
            "/invocations/1/clients/client-001/receipt",
            json={
                "client_status": "download_failed",
                "error_detail": "should be ignored after success",
            },
        )

    assert first_receipt.status_code == 202
    assert second_receipt.status_code == 202
    assert third_receipt.status_code == 202
    assert state.client_outcomes["client-001"].client_status == ClientStatus.SUCCESS
    assert state.client_outcomes["client-001"].error_detail is None


def test_controller_rejects_late_receipt_after_timeout() -> None:
    state = ControllerState(
        invocation_instance=1,
        expected_client_ids=expected_client_ids(1),
        expected_bundle=DEFAULT_BUNDLE,
    )
    state._receipt_channel_open = False

    with TestClient(create_controller_app(state)) as client:
        response = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=_success_receipt_payload(),
        )

    assert response.status_code == 410
    assert response.json() == {
        "status": "rejected",
        "detail": "Receipt channel is closed for this invocation.",
    }


def test_controller_keeps_receipt_channel_open_until_every_expected_client_reports() -> None:
    state = ControllerState(
        invocation_instance=1,
        expected_client_ids=expected_client_ids(2),
        expected_bundle=DEFAULT_BUNDLE,
    )

    with TestClient(create_controller_app(state)) as client:
        first_receipt = client.post(
            "/invocations/1/clients/client-001/receipt",
            json=_success_receipt_payload(),
        )
        second_receipt = client.post(
            "/invocations/1/clients/client-002/receipt",
            json=_success_receipt_payload(),
        )
        late_receipt = client.post(
            "/invocations/1/clients/client-002/receipt",
            json=_success_receipt_payload(),
        )

    assert first_receipt.status_code == 202
    assert second_receipt.status_code == 202
    assert late_receipt.status_code == 410
    assert state.expected_client_ids == ("client-001", "client-002")
    assert set(state.client_outcomes) == {"client-001", "client-002"}


def _success_receipt_payload() -> dict[str, object]:
    return {
        "client_status": "success",
        "reported_bundle": DEFAULT_BUNDLE.to_payload(),
    }
