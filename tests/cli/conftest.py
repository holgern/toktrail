from __future__ import annotations

import pytest

from tests.cli.helpers import (
    _toml_path_value,
)


@pytest.fixture(autouse=True)
def isolate_default_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    default_config = tmp_path / "default-config.toml"
    default_config.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(tmp_path / "missing-opencode.db")}"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TOKTRAIL_CONFIG", str(default_config))
    for key in (
        "TOKTRAIL_PI_SESSIONS",
        "TOKTRAIL_COPILOT_FILE",
        "COPILOT_OTEL_FILE_EXPORTER_PATH",
        "TOKTRAIL_COPILOT_OTEL_DIR",
        "TOKTRAIL_CODE_SESSIONS",
        "CODE_HOME",
        "TOKTRAIL_CODEX_SESSIONS",
        "CODEX_HOME",
        "TOKTRAIL_GOOSE_SESSIONS",
        "GOOSE_PATH_ROOT",
        "TOKTRAIL_HARNESSBRIDGE_SESSIONS",
        "TOKTRAIL_DROID_SESSIONS",
        "TOKTRAIL_AMP_THREADS",
    ):
        monkeypatch.delenv(key, raising=False)
