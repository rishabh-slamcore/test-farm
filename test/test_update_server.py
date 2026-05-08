"""Update Server behavior tests."""

import hashlib

from fastapi.testclient import TestClient

from test_farm.subjects.update_server import create_update_server_app


def test_update_server_reports_health() -> None:
    with TestClient(create_update_server_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_update_server_serves_baseline_bundle_and_matching_manifest() -> None:
    with TestClient(create_update_server_app()) as client:
        bundle_response = client.get("/bundles/baseline")
        manifest_response = client.get("/bundles/baseline/manifest")

    bundle_bytes = bundle_response.content

    assert bundle_response.status_code == 200
    assert bundle_response.headers["content-type"] == "application/octet-stream"
    assert manifest_response.status_code == 200
    assert manifest_response.json() == {
        "bundle_id": "baseline",
        "byte_count": len(bundle_bytes),
        "checksum": hashlib.sha256(bundle_bytes).hexdigest(),
    }
