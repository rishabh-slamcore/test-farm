"""Update Server behavior tests."""

import asyncio
import hashlib
from pathlib import Path

import httpx
from fastapi import FastAPI

from test_farm.bundles import FileBackedBundleSource
from test_farm.subjects.update_server import create_update_server_app


def test_update_server_reports_health() -> None:
    response = asyncio.run(_get(create_update_server_app(), "/health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_update_server_serves_baseline_bundle_and_matching_manifest() -> None:
    bundle_response = asyncio.run(_get(create_update_server_app(), "/bundles/baseline"))
    manifest_response = asyncio.run(
        _get(create_update_server_app(), "/bundles/baseline/manifest")
    )

    bundle_bytes = bundle_response.content

    assert bundle_response.status_code == 200
    assert bundle_response.headers["content-type"] == "application/octet-stream"
    assert manifest_response.status_code == 200
    assert manifest_response.json() == {
        "bundle_id": "baseline",
        "byte_count": len(bundle_bytes),
        "checksum": hashlib.sha256(bundle_bytes).hexdigest(),
    }


def test_update_server_serves_bundle_named_for_bundle_id_from_file_backed_directory(
    tmp_path: Path,
) -> None:
    bundle_bytes = b"bundle bytes from mounted file\n"
    (tmp_path / "baseline").write_bytes(bundle_bytes)
    app = create_update_server_app(bundle_source=FileBackedBundleSource(tmp_path))

    bundle_response = asyncio.run(_get(app, "/bundles/baseline"))
    manifest_response = asyncio.run(_get(app, "/bundles/baseline/manifest"))
    missing_bundle_response = asyncio.run(_get(app, "/bundles/not-baseline"))

    assert bundle_response.status_code == 200
    assert bundle_response.content == bundle_bytes
    assert manifest_response.status_code == 200
    assert manifest_response.json() == {
        "bundle_id": "baseline",
        "byte_count": len(bundle_bytes),
        "checksum": hashlib.sha256(bundle_bytes).hexdigest(),
    }
    assert missing_bundle_response.status_code == 404


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)
