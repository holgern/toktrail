from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from toktrail.cli import app


def test_cli_machine_status_and_set_name(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    machine_config = tmp_path / "machine.toml"

    init_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--machine-config",
            str(machine_config),
            "init",
        ],
    )
    assert init_result.exit_code == 0, init_result.output

    set_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--machine-config",
            str(machine_config),
            "machine",
            "set-name",
            "thinkpad",
        ],
    )
    assert set_result.exit_code == 0, set_result.output
    assert machine_config.exists()
    assert 'name = "thinkpad"' in machine_config.read_text(encoding="utf-8")

    status_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--machine-config",
            str(machine_config),
            "machine",
            "status",
            "--json",
        ],
    )
    assert status_result.exit_code == 0, status_result.output
    payload = json.loads(status_result.output)
    assert payload["name"] == "thinkpad"
    assert payload["is_local"] is True
    assert payload["config_path"] == str(machine_config)
