from __future__ import annotations

import json
from copy import deepcopy

from typer.testing import CliRunner

from tests.cli.helpers import (
    _toml_path_value,
    create_pi_session_file,
)
from tests.helpers import (
    VALID_ASSISTANT,
    create_opencode_db,
    insert_message,
)
from toktrail.api.sessions import init_state, start_run
from toktrail.cli import app


def test_cli_watch_imports_configured_harnesses_and_prints_token_delta(
    tmp_path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    opencode_db = tmp_path / "opencode.db"
    pi_file = tmp_path / "sessions" / "encoded-cwd" / "session.jsonl"
    config_path = tmp_path / "toktrail.toml"

    conn = create_opencode_db(opencode_db)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()

    create_pi_session_file(pi_file)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode", "pi"]
missing_source = "error"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(opencode_db)}"
pi = "{_toml_path_value(pi_file)}"
""".strip(),
        encoding="utf-8",
    )

    init_state(state_db)
    start_run(state_db, name="watch-test", started_at_ms=0)

    def interrupt_after_first_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", interrupt_after_first_sleep)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "watch",
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Watching configured harnesses" in result.output
    assert "tokens" in result.output
    assert "input" in result.output or "in=" in result.output
    assert "output" in result.output or "out=" in result.output
    assert "opencode" in result.output.lower()
    assert "rows imported" not in result.output
    assert "rows seen" not in result.output


def test_cli_watch_does_not_print_idle_duplicate_imports(
    tmp_path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    opencode_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"

    conn = create_opencode_db(opencode_db)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()

    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "error"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(opencode_db)}"
""".strip(),
        encoding="utf-8",
    )

    init_state(state_db)
    start_run(state_db, name="watch-idle", started_at_ms=0)

    sleep_calls = 0

    def sleep_then_interrupt(_interval: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", sleep_then_interrupt)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "watch",
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("+1 msgs") == 1, result.output
    assert "rows imported" not in result.output


def test_cli_watch_json_outputs_delta_events_only(
    tmp_path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    opencode_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"

    conn = create_opencode_db(opencode_db)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()

    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "error"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(opencode_db)}"
""".strip(),
        encoding="utf-8",
    )

    init_state(state_db)
    start_run(state_db, name="watch-json", started_at_ms=0)

    def interrupt_after_first_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", interrupt_after_first_sleep)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "watch",
            "--json",
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    events = [json.loads(line) for line in lines]
    assert len(events) >= 1
    assert all(event["type"] == "usage_delta" for event in events)
    assert events[0]["delta"]["total"] > 0
    by_harness = events[0]["by_harness"]
    assert len(by_harness) == 1
    assert by_harness[0]["harness"] == "opencode"
    assert by_harness[0]["input"] > 0
    assert by_harness[0]["output"] > 0
    assert by_harness[0]["cache_read"] > 0


def test_cli_watch_json_includes_cache_output_delta(
    tmp_path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    opencode_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"

    conn = create_opencode_db(opencode_db)
    payload = deepcopy(VALID_ASSISTANT)
    payload["tokens"] = {
        "input": 100,
        "output": 5,
        "reasoning": 0,
        "cache": {"read": 10, "write": 0, "output": 7},
    }
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=payload,
    )
    conn.commit()
    conn.close()

    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "error"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(opencode_db)}"
""".strip(),
        encoding="utf-8",
    )

    init_state(state_db)
    start_run(state_db, name="watch-json-cache-output", started_at_ms=0)

    def interrupt_after_first_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", interrupt_after_first_sleep)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "watch",
            "--json",
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    payloads = [json.loads(line) for line in lines]
    assert payloads
    assert payloads[0]["delta"]["cache_output"] == 7
    assert payloads[0]["by_harness"][0]["cache_output"] == 7
