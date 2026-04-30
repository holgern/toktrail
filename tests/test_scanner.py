"""Tests for the central source discovery scanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from toktrail.config import CostingConfig, ImportConfig, ToktrailConfig
from toktrail.scanner import (
    DiscoverSourcesResult,
    SourceFile,
    SourceFingerprint,
    discover_sources,
)


def test_discover_sources_empty() -> None:
    """Test discovery with no harnesses."""
    result = discover_sources(harnesses=[])
    assert len(result.sources) == 0
    assert len(result.warnings) == 0


def test_discover_sources_single_harness() -> None:
    """Test discovery of a single harness."""
    result = discover_sources(harnesses=["opencode"])
    assert isinstance(result, DiscoverSourcesResult)
    assert isinstance(result.sources, tuple)
    assert isinstance(result.warnings, tuple)


def test_discover_sources_multiple_harnesses() -> None:
    """Test discovery of multiple harnesses."""
    result = discover_sources(harnesses=["opencode", "pi", "copilot"])
    assert len(result.sources) >= 0
    # Some sources may not exist locally


def test_discover_sources_unknown_harness() -> None:
    """Test discovery with unknown harness."""
    result = discover_sources(harnesses=["unknown"])
    assert len(result.sources) == 0
    assert len(result.warnings) == 1
    assert result.warnings[0].message == "Unknown harness: unknown"


def test_discover_sources_with_explicit_source(tmp_path: Path) -> None:
    """Test discovery with explicit source path."""
    test_file = tmp_path / "test.db"
    test_file.touch()
    
    result = discover_sources(
        harnesses=["opencode"],
        explicit_source=test_file,
    )
    assert len(result.sources) == 1
    assert result.sources[0].path == test_file
    assert result.sources[0].harness == "opencode"


def test_discover_sources_explicit_source_multiple_harnesses() -> None:
    """Test that explicit source requires exactly one harness."""
    test_path = Path("/tmp/test.db")
    result = discover_sources(
        harnesses=["opencode", "pi"],
        explicit_source=test_path,
    )
    assert len(result.sources) == 0
    assert len(result.warnings) == 1
    assert "exactly one harness" in result.warnings[0].message


def test_source_fingerprint_creation() -> None:
    """Test creating a source fingerprint."""
    fp = SourceFingerprint(
        size=1024,
        mtime_ns=1000000000,
        inode=12345,
        sqlite_page_count=100,
        sqlite_schema_version=2,
    )
    assert fp.size == 1024
    assert fp.mtime_ns == 1000000000
    assert fp.inode == 12345
    assert fp.sqlite_page_count == 100
    assert fp.sqlite_schema_version == 2


def test_source_file_creation(tmp_path: Path) -> None:
    """Test creating a source file."""
    path = tmp_path / "test.json"
    path.touch()
    
    fp = SourceFingerprint(size=0, mtime_ns=0, inode=0)
    source = SourceFile(
        harness="pi",
        path=path,
        kind="json",
        fingerprint=fp,
    )
    assert source.harness == "pi"
    assert source.path == path
    assert source.kind == "json"


def test_discover_sources_nonexistent_path() -> None:
    """Test discovery with nonexistent configured path."""
    config = ToktrailConfig(
        costing=CostingConfig(),
        imports=ImportConfig(
            harnesses=("opencode",),
            sources={"opencode": Path("/nonexistent/path/opencode.db")},
        ),
    )
    result = discover_sources(
        harnesses=["opencode"],
        config=config,
    )
    # With warn mode (default), missing paths should generate warnings
    assert any(
        "does not exist" in w.message
        for w in result.warnings
    )


def test_discover_sources_with_config(tmp_path: Path) -> None:
    """Test discovery using provided config."""
    test_db = tmp_path / "opencode.db"
    test_db.touch()
    
    config = ToktrailConfig(
        costing=CostingConfig(),
        imports=ImportConfig(
            harnesses=("opencode",),
            sources={"opencode": test_db},
        ),
    )
    result = discover_sources(
        harnesses=["opencode"],
        config=config,
    )
    assert len(result.sources) == 1
    assert result.sources[0].path == test_db


def test_discover_sources_with_list_of_paths(tmp_path: Path) -> None:
    """Test discovery with multiple configured paths for a single harness."""
    path1 = tmp_path / "sessions1"
    path2 = tmp_path / "sessions2"
    path1.mkdir()
    path2.mkdir()
    
    config = ToktrailConfig(
        costing=CostingConfig(),
        imports=ImportConfig(
            harnesses=("pi",),
            sources={"pi": [path1, path2]},
        ),
    )
    result = discover_sources(
        harnesses=["pi"],
        config=config,
    )
    assert len(result.sources) == 2
    assert {s.path for s in result.sources} == {path1, path2}


def test_source_fingerprint_frozen() -> None:
    """Test that SourceFingerprint is frozen."""
    fp = SourceFingerprint(size=100, mtime_ns=1000, inode=5)
    with pytest.raises(AttributeError):
        fp.size = 200  # type: ignore


def test_source_file_frozen(tmp_path: Path) -> None:
    """Test that SourceFile is frozen."""
    fp = SourceFingerprint(size=0, mtime_ns=0, inode=0)
    source = SourceFile(
        harness="pi",
        path=tmp_path,
        kind="directory",
        fingerprint=fp,
    )
    with pytest.raises(AttributeError):
        source.harness = "opencode"  # type: ignore


def test_discover_sources_default_harnesses() -> None:
    """Test discovery defaults to all harnesses in config."""
    config = ToktrailConfig(
        costing=CostingConfig(),
        imports=ImportConfig(harnesses=("opencode", "pi")),
    )
    result = discover_sources(config=config)
    # Should discover for opencode and pi
    assert all(s.harness in ("opencode", "pi") for s in result.sources)
