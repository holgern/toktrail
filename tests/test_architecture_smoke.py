from __future__ import annotations


def test_facade_modules_import() -> None:
    from toktrail.cli import cli_main
    from toktrail.config import load_resolved_toktrail_config
    from toktrail.db import SCHEMA_VERSION, connect
    from toktrail.sync import export_state_archive

    assert callable(cli_main)
    assert callable(load_resolved_toktrail_config)
    assert SCHEMA_VERSION == 14
    assert callable(connect)
    assert callable(export_state_archive)


def test_public_api_imports() -> None:
    from toktrail import api

    assert hasattr(api, "TokenBreakdown")
    assert hasattr(api, "usage_report")
    assert hasattr(api, "usage_series_report")
