"""Tests for session search index: redaction, indexing, searching, and clearing."""

from __future__ import annotations

import sqlite3

from toktrail.session_search import (
    SessionIndexItem,
    clear_session_index,
    index_session_items,
    redact_text,
    search_session_index,
)


def _item(
    *,
    harness: str = "pi",
    session: str = "s1",
    ordinal: int = 1,
    kind: str = "user_prompt",
    content: str = "test",
) -> SessionIndexItem:
    return SessionIndexItem(
        origin_machine_id="m1",
        harness=harness,
        source_session_id=session,
        ordinal=ordinal,
        kind=kind,
        created_ms=1000,
        content_redacted=content,
        source_fingerprint=None,
    )


class TestRedactText:
    def test_no_secrets(self) -> None:
        assert redact_text("hello world") == "hello world"

    def test_sk_key_redacted(self) -> None:
        result = redact_text("use sk-abcdefghijklmnopqrstu")
        assert "[REDACTED]" in result

    def test_aws_key_redacted(self) -> None:
        result = redact_text("AKIA1234567890ABCDEF")
        assert "[REDACTED]" in result

    def test_email_redacted(self) -> None:
        result = redact_text("dev@example.com is the address")
        assert "[REDACTED]" in result

    def test_truncation(self) -> None:
        long_text = "a" * 600
        result = redact_text(long_text, max_chars=500)
        assert len(result) == 503  # 500 + "..."


class TestIndexAndSearch:
    def test_empty_index(self, tmp_path) -> None:
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        results = search_session_index(conn, "test")
        assert results == ()

    def test_index_and_search_substring(self, tmp_path) -> None:
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        items = (
            _item(content="failed to import module"),
            _item(content="success", ordinal=2),
        )
        index_session_items(conn, items)
        results = search_session_index(conn, "import")
        assert len(results) == 1
        assert "import" in results[0].content_redacted

    def test_search_no_match(self, tmp_path) -> None:
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        items = (_item(content="hello world"),)
        index_session_items(conn, items)
        results = search_session_index(conn, "nonexistent")
        assert results == ()

    def test_filter_by_harness(self, tmp_path) -> None:
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        items = (
            _item(harness="pi", session="s1", content="pi error"),
            _item(harness="codex", session="s2", content="codex error"),
        )
        index_session_items(conn, items)
        results = search_session_index(conn, "error", harness="pi")
        assert len(results) == 1
        assert results[0].harness == "pi"

    def test_clear_index(self, tmp_path) -> None:
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        items = (_item(content="test"),)
        index_session_items(conn, items)
        assert len(search_session_index(conn, "test")) == 1
        clear_session_index(conn)
        assert search_session_index(conn, "test") == ()

    def test_idempotent_insert(self, tmp_path) -> None:
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        item = _item(content="test")
        index_session_items(conn, (item,))
        index_session_items(conn, (item,))
        results = search_session_index(conn, "test")
        assert len(results) == 1

    def test_item_as_dict(self) -> None:
        item = _item(content="hello")
        d = item.as_dict()
        assert d["kind"] == "user_prompt"
        assert d["content_redacted"] == "hello"
        assert d["harness"] == "pi"


class TestConfigDisabled:
    def test_search_without_config_raises(self, tmp_path, monkeypatch) -> None:
        from toktrail.api.config import init_config
        from toktrail.api.sessions import init_state

        db_path = tmp_path / "toktrail.db"
        config_path = tmp_path / "toktrail.toml"
        init_state(db_path)
        init_config(config_path, template="copilot")
        monkeypatch.setenv("TOKTRAIL_DB", str(db_path))
        monkeypatch.setenv("TOKTRAIL_CONFIG", str(config_path))

        from typer.testing import CliRunner

        from toktrail.cli import app

        result = CliRunner().invoke(app, ["session", "search", "test"])
        assert result.exit_code != 0
        assert "disabled" in result.output.lower()
