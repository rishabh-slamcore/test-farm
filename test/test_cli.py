"""CLI tests for timed-out baseline invocation behavior."""

import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from test_farm.cli import app


def test_run_writes_timed_out_result_file_for_one_client(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
            "--controller-reportback-url",
            "http://controller.example:8080",
        ],
    )
    result_file = tmp_path / "results" / "result_1.json"
    payload = json.loads(result_file.read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert result_file.exists()
    assert payload["invocation_instance"] == 1
    assert payload["scenario_file"] == str(scenario_file)
    assert payload["invocation_status"] == "failed"
    assert payload["expected_bundle"] == {
        "bundle_id": "baseline",
        "byte_count": None,
        "checksum": None,
    }
    assert payload["clients"] == [
        {
            "client_id": "client-001",
            "client_status": "timed_out",
            "bundle_id": "baseline",
            "error_detail": "No verified receipt received before timeout.",
        }
    ]
    assert "started_at" in payload
    assert "finished_at" in payload


def test_run_increments_invocation_instance_from_existing_result_files(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runner = CliRunner()
    scenario_file = tmp_path / "baseline.yaml"
    results_dir = tmp_path / "results"
    scenario_file.write_text("client_count: 1\n", encoding="utf-8")
    results_dir.mkdir()
    (results_dir / "result_2.json").write_text("{}\n", encoding="utf-8")
    (results_dir / "notes.txt").write_text("ignore me\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            str(scenario_file),
            "--controller-bind-address",
            "127.0.0.1:8080",
            "--controller-reportback-url",
            "http://controller.example:8080",
        ],
    )

    assert result.exit_code == 1
    assert (results_dir / "result_3.json").exists()
