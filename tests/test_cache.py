"""Tests for source file cache functionality."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toktrail.cache import CacheEntry, CacheKey, fingerprint_for_path, is_cache_valid
from toktrail.models import TokenBreakdown, UsageEvent


def test_fingerprint_for_path_exists(tmp_path: Path) -> None:
    """Fingerprint should combine size, mtime, and content hash."""
    test_file = tmp_path / "test.jsonl"
    test_file.write_text('{"model": "test"}')

    fp = fingerprint_for_path(test_file)
    assert fp != ""
    assert ":" in fp  # format: size:mtime:hash

    parts = fp.split(":")
    assert len(parts) == 3
    assert parts[0].isdigit()  # size
    assert parts[1].isdigit()  # mtime
    assert len(parts[2]) > 0  # content hash


def test_fingerprint_for_path_missing() -> None:
    """Fingerprint for missing file should be empty."""
    fp = fingerprint_for_path(Path("/nonexistent/path/file.txt"))
    assert fp == ""


def test_fingerprint_changes_on_content_change(tmp_path: Path) -> None:
    """Fingerprint should change when file content changes."""
    test_file = tmp_path / "test.jsonl"
    test_file.write_text("first")
    fp1 = fingerprint_for_path(test_file)

    test_file.write_text("second different content")
    fp2 = fingerprint_for_path(test_file)

    assert fp1 != fp2


def test_cache_key_creation() -> None:
    """CacheKey should combine harness, path, and parser version."""
    key = CacheKey(
        harness="codex",
        canonical_source_path=Path("/home/user/codex_events.jsonl"),
        parser_version=1,
    )
    assert key.harness == "codex"
    assert key.canonical_source_path == Path("/home/user/codex_events.jsonl")
    assert key.parser_version == 1


def test_cache_entry_creation(tmp_path: Path) -> None:
    """CacheEntry should store events with metadata."""
    from decimal import Decimal

    test_file = tmp_path / "test.jsonl"
    test_file.write_text('{"model": "test"}')
    fp = fingerprint_for_path(test_file)

    event = UsageEvent(
        harness="codex",
        source_session_id="sess-1",
        source_row_id="row-1",
        source_message_id=None,
        source_dedup_key="dedup-1",
        global_dedup_key="global-1",
        fingerprint_hash="hash-1",
        model_id="model-1",
        provider_id="provider-1",
        thinking_level=None,
        agent=None,
        created_ms=0,
        completed_ms=None,
        tokens=TokenBreakdown(input=100, output=50),
        source_cost_usd=Decimal("0.001"),
        raw_json=None,
    )

    entry = CacheEntry(
        events=[event],
        parser_warnings=[],
        fingerprint=fp,
        parser_version=1,
        schema_version=1,
    )
    assert len(entry.events) == 1
    assert entry.fingerprint == fp
    assert entry.parser_version == 1


def test_is_cache_valid_with_matching_fingerprint(tmp_path: Path) -> None:
    """Cache should be valid if parser version and fingerprint match."""
    test_file = tmp_path / "test.jsonl"
    test_file.write_text("content")
    fp = fingerprint_for_path(test_file)

    entry = CacheEntry(
        events=[],
        parser_warnings=[],
        fingerprint=fp,
        parser_version=1,
    )

    assert is_cache_valid(entry, test_file, 1) is True


def test_is_cache_invalid_with_different_parser_version(tmp_path: Path) -> None:
    """Cache should be invalid if parser version differs."""
    test_file = tmp_path / "test.jsonl"
    test_file.write_text("content")
    fp = fingerprint_for_path(test_file)

    entry = CacheEntry(
        events=[],
        parser_warnings=[],
        fingerprint=fp,
        parser_version=1,
    )

    assert is_cache_valid(entry, test_file, 2) is False


def test_is_cache_invalid_with_different_fingerprint(tmp_path: Path) -> None:
    """Cache should be invalid if fingerprint differs."""
    test_file = tmp_path / "test.jsonl"
    test_file.write_text("content1")
    entry = CacheEntry(
        events=[],
        parser_warnings=[],
        fingerprint="old_fingerprint_value",
        parser_version=1,
    )

    test_file.write_text("content2")
    assert is_cache_valid(entry, test_file, 1) is False


def test_is_cache_invalid_for_missing_file(tmp_path: Path) -> None:
    """Cache should be invalid if file no longer exists."""
    test_file = tmp_path / "test.jsonl"
    test_file.write_text("content")
    fp = fingerprint_for_path(test_file)

    entry = CacheEntry(
        events=[],
        parser_warnings=[],
        fingerprint=fp,
        parser_version=1,
    )

    test_file.unlink()
    assert is_cache_valid(entry, test_file, 1) is False
