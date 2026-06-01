from __future__ import annotations

from pathlib import Path


def test_facade_modules_import() -> None:
    from toktrail.cli import cli_main
    from toktrail.config import load_resolved_toktrail_config
    from toktrail.db import SCHEMA_VERSION, connect
    from toktrail.sync import export_state_archive

    assert callable(cli_main)
    assert callable(load_resolved_toktrail_config)
    assert SCHEMA_VERSION == 17
    assert callable(connect)
    assert callable(export_state_archive)


def test_public_api_imports() -> None:
    from toktrail import api

    assert hasattr(api, "TokenBreakdown")
    assert hasattr(api, "usage_report")
    assert hasattr(api, "usage_series_report")


def test_compatibility_facades_keep_expected_exports() -> None:
    import toktrail.api.models as models
    import toktrail.cli as cli
    import toktrail.config as config
    import toktrail.db as db
    from toktrail._db.core_db import SCHEMA_VERSION as CORE_SCHEMA_VERSION

    assert callable(cli.cli_main)
    assert callable(config.load_resolved_toktrail_config)
    assert callable(db.connect)
    assert db.SCHEMA_VERSION == CORE_SCHEMA_VERSION
    assert hasattr(models, "TokenBreakdown")
    assert hasattr(models, "UsageEvent")


def test_legacy_compatibility_facades_stay_small() -> None:
    root = Path(__file__).resolve().parents[1]
    legacy_paths = (
        root / "toktrail" / "api" / "model_parts" / "legacy_models.py",
        root / "toktrail" / "config_parts" / "legacy_config.py",
        root / "toktrail" / "_db" / "legacy_db.py",
    )
    max_lines = 120
    for path in legacy_paths:
        line_count = sum(1 for _ in path.read_text(encoding="utf-8").splitlines())
        assert line_count <= max_lines, (
            f"{path} has {line_count} lines (max {max_lines})"
        )
