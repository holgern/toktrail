from __future__ import annotations

from toktrail.tui.layout import resolve_tui_display


def test_resolve_tui_display_full_for_large_terminal(monkeypatch) -> None:
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setenv("PREFIX", "/usr")
    assert resolve_tui_display("auto", columns=120, rows=40).mode == "full"


def test_resolve_tui_display_compact_for_narrow_terminal() -> None:
    assert resolve_tui_display("auto", columns=80, rows=24).mode == "compact"


def test_resolve_tui_display_micro_for_tiny_terminal() -> None:
    assert resolve_tui_display("auto", columns=60, rows=18).mode == "micro"


def test_resolve_tui_display_respects_explicit_full() -> None:
    assert resolve_tui_display("full", columns=60, rows=18).mode == "full"


def test_resolve_tui_display_termux_prefers_compact(monkeypatch) -> None:
    monkeypatch.setenv("TERMUX_VERSION", "0.118")
    assert resolve_tui_display("auto", columns=120, rows=40).mode == "compact"
