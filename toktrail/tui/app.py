# mypy: ignore-errors
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import ContentSwitcher, Footer, Header, Static, Tab, Tabs

from toktrail.errors import InvalidAPIUsageError
from toktrail.periods import resolve_timezone
from toktrail.tui.layout import TuiDisplay, TuiMode, resolve_tui_display
from toktrail.tui.panes.areas import AreasPane
from toktrail.tui.panes.config import ConfigPane
from toktrail.tui.panes.dashboard import DashboardPane
from toktrail.tui.panes.prices import PricesPane
from toktrail.tui.panes.sessions import SessionsPane
from toktrail.tui.panes.subscriptions import SubscriptionsPane
from toktrail.tui.screens.area_form import AreaFormScreen
from toktrail.tui.screens.confirm import ConfirmScreen
from toktrail.tui.screens.help import HelpScreen
from toktrail.tui.screens.price_form import PriceFormScreen
from toktrail.tui.services import DashboardView, ToktrailTuiService
from toktrail.tui.state import ToktrailTuiState


class ToktrailTuiApp(App[None]):
    TITLE = "toktrail"
    CSS_PATH = "styles/toktrail.tcss"

    BINDINGS = [
        Binding("1", "switch_pane('dashboard')", "Dashboard", show=False),
        Binding("2", "switch_pane('sessions')", "Sessions", show=False),
        Binding("3", "switch_pane('areas')", "Areas", show=False),
        Binding("4", "switch_pane('prices')", "Prices", show=False),
        Binding("5", "switch_pane('subscriptions')", "Subscriptions", show=False),
        Binding("6", "switch_pane('config')", "Config", show=False),
        Binding("t", "switch_dashboard('today')", "Today", show=False),
        Binding("d", "switch_dashboard('daily')", "Daily", show=False),
        Binding("w", "switch_dashboard('weekly')", "Weekly", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "create_area", "Create area"),
        Binding("u", "set_active_area", "Use area"),
        Binding("c", "clear_active_area", "Clear area"),
        Binding("l", "assign_latest", "Assign latest"),
        Binding("s", "assign_selected_session", "Assign selected"),
        Binding("e", "edit_selected_price", "Edit price"),
        Binding("p", "toggle_price_subview", "Toggle prices view", show=False),
        Binding("y", "copy_current_view", "Copy"),
        Binding("Y", "export_current_view", "Export"),
        Binding("i", "toggle_details", "Details", show=False),
        Binding("enter", "toggle_details", "Details", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("h", "help", "Help", show=False),
        Binding("left", "day_back", "Day back", show=False, priority=True),
        Binding("right", "day_forward", "Day forward", show=False, priority=True),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        db_path: Path,
        config_path: Path,
        prices_path: Path,
        prices_dir: Path,
        subscriptions_path: Path,
        initial_area: str | None = None,
        timezone_name: str | None = None,
        utc: bool = False,
        refresh_on_start: bool = True,
        tui_mode: TuiMode = "auto",
    ) -> None:
        super().__init__()
        self.state = ToktrailTuiState(
            db_path=db_path,
            config_path=config_path,
            prices_path=prices_path,
            prices_dir=prices_dir,
            subscriptions_path=subscriptions_path,
            initial_area=initial_area,
            timezone_name=timezone_name,
            utc=utc,
            refresh_on_start=refresh_on_start,
            tui_mode=tui_mode,
        )
        self.service = ToktrailTuiService(self.state)
        self._dashboard = DashboardPane(id="dashboard")
        self._sessions = SessionsPane(id="sessions")
        self._areas = AreasPane(id="areas")
        self._prices = PricesPane(id="prices")
        self._subscriptions = SubscriptionsPane(id="subscriptions")
        self._config = ConfigPane(id="config")
        self._config = ConfigPane(id="config")
        self._tui_display: TuiDisplay | None = None
        self._compact_details_visible = False
        self._compact_bar = Static("", id="compact-bar")
        self._status = Static("", id="status")
        self._dashboard_view: DashboardView = "today"
        self._date_offset: int = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield self._compact_bar
        yield Tabs(
            Tab("Dashboard", id="tab-dashboard"),
            Tab("Sessions", id="tab-sessions"),
            Tab("Areas", id="tab-areas"),
            Tab("Prices", id="tab-prices"),
            Tab("Subscriptions", id="tab-subscriptions"),
            Tab("Config", id="tab-config"),
            active="tab-dashboard",
            id="tabs",
        )
        yield ContentSwitcher(
            self._dashboard,
            self._sessions,
            self._areas,
            self._prices,
            self._subscriptions,
            self._config,
            initial="dashboard",
            id="content",
        )
        yield self._status
        yield Footer()

    def on_mount(self) -> None:
        self._apply_display()
        self._refresh_views()
        if self.state.initial_area:
            self.service.set_active_area(self.state.initial_area)
            self._refresh_views()
        if self.state.refresh_on_start:
            self.action_refresh()
        self._update_compact_bar()

    def on_resize(self, event: events.Resize) -> None:
        del event
        previous_mode = None if self._tui_display is None else self._tui_display.mode
        self._apply_display()
        current_mode = None if self._tui_display is None else self._tui_display.mode
        if current_mode != previous_mode:
            self._refresh_views()
        self._update_compact_bar()

    def action_switch_pane(self, pane_id: str) -> None:
        content = self.query_one("#content", ContentSwitcher)
        content.current = pane_id
        tabs = self.query_one("#tabs", Tabs)
        tabs.active = f"tab-{pane_id}"
        self._update_compact_bar()
        self._focus_table_if_needed(pane_id)
        self._update_status_with_date()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        tab_id = event.tab.id
        if not isinstance(tab_id, str):
            return
        if not tab_id.startswith("tab-"):
            return
        pane_id = tab_id.removeprefix("tab-")
        content = self.query_one("#content", ContentSwitcher)
        content.current = pane_id
        self._update_compact_bar()
        self._focus_table_if_needed(pane_id)
        self._update_status_with_date()

    def action_help(self) -> None:
        display = self._tui_display or self._resolve_display()
        self.push_screen(HelpScreen(display.mode))

    def action_refresh(self) -> None:
        try:
            self.service.refresh()
            self._refresh_views()
            self._status.update("Refresh completed.")
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            self._status.update(f"Refresh failed: {exc}")

    def action_create_area(self) -> None:
        self.push_screen(AreaFormScreen(), self._on_area_form_saved)

    def action_set_active_area(self) -> None:
        path = self._areas.selected_area_path
        if not path:
            self._status.update("No area selected.")
            return
        self.service.set_active_area(path)
        self._refresh_views()
        self._status.update(f"Active area set: {path}")

    def action_clear_active_area(self) -> None:
        def _after_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            self.service.clear_active_area()
            self._refresh_views()
            self._status.update("Active area cleared.")

        self.push_screen(ConfirmScreen("Clear active area?"), _after_confirm)

    def action_assign_latest(self) -> None:
        path = self._areas.selected_area_path
        if not path:
            self._status.update("No area selected.")
            return
        self.service.assign_latest_session_to_area(path)
        self._refresh_views()
        self._status.update(f"Assigned latest session to {path}.")

    def action_assign_selected_session(self) -> None:
        path = self._areas.selected_area_path
        key = self._sessions.selected_session_key
        if not path or not key:
            self._status.update("Select an area and session first.")
            return
        self.service.assign_session_to_area(path, key)
        self._refresh_views()
        self._status.update(f"Assigned {key} to {path}.")

    def action_edit_selected_price(self) -> None:
        seed = self._prices.seed_manual_price_row()
        if seed is None:
            self._status.update("No unconfigured model selected.")
            return
        self.push_screen(PriceFormScreen(seed), self._on_price_saved)

    def action_switch_dashboard(self, view: str) -> None:
        if view not in {"today", "daily", "weekly"}:
            self._status.update(f"Unknown dashboard: {view}")
            return
        self._dashboard_view = cast(DashboardView, view)
        self._dashboard.set_data(self.service.dashboard(self._dashboard_view))
        self.action_switch_pane("dashboard")
        self._status.update(f"Dashboard view: {view}")

    def action_toggle_price_subview(self) -> None:
        selected = self._prices.toggle_subview()
        self._status.update(f"Prices view: {selected}")

    def action_toggle_details(self) -> None:
        display = self._tui_display or self._resolve_display()
        if display.mode == "full":
            self._status.update("Details are always visible in full mode.")
            return
        if display.mode == "micro":
            self._status.update(self._micro_detail_preview())
            return
        self._compact_details_visible = not self._compact_details_visible
        self._apply_display()
        state = "shown" if self._compact_details_visible else "hidden"
        self._status.update(f"Details {state}.")

    def action_copy_current_view(self) -> None:
        pane_id, text = self._current_pane_text()
        if not text.strip():
            self._status.update("Current view is empty.")
            return
        copied = self._copy_to_clipboard(text)
        fallback = self._copy_fallback_path()
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(text, encoding="utf-8")
        if copied:
            self._status.update(f"Copied {pane_id} view to clipboard.")
            return
        self._status.update(
            f"Clipboard unavailable; wrote copy fallback to {fallback}."
        )

    def action_export_current_view(self) -> None:
        pane_id, text = self._current_pane_text()
        if not text.strip():
            self._status.update("Current view is empty.")
            return
        export_path = self._export_dir() / f"toktrail-{pane_id}.txt"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(text, encoding="utf-8")
        self._status.update(f"Exported current view: {export_path}")

    def _on_area_form_saved(self, value: str | None) -> None:
        if not value:
            self._status.update("Area creation cancelled.")
            return
        self.service.create_area(value)
        self._refresh_views()
        self._status.update(f"Area created: {value}")

    def _on_price_saved(self, row) -> None:
        if row is None:
            self._status.update("Price edit cancelled or invalid.")
            return
        try:
            written = self.service.upsert_manual_price(row)
        except InvalidAPIUsageError as exc:
            self._status.update(f"Invalid price: {exc}")
            return
        self._refresh_views()
        self._status.update(f"Saved to {written}")

    def _refresh_views(self) -> None:
        self._apply_display()
        self._dashboard.set_data(self.service.dashboard(self._dashboard_view))
        self._sessions.set_data(self.service.sessions(self._date_offset))
        self._areas.set_data(self.service.areas(self._date_offset))
        self._prices.set_data(self.service.prices())
        self._subscriptions.set_data(self.service.subscriptions())
        self._config.set_data(self.service.config())
        self._update_compact_bar()
        self._update_status_with_date()

    def _current_pane_text(self) -> tuple[str, str]:
        content = self.query_one("#content", ContentSwitcher)
        pane_id = content.current or "dashboard"
        pane = self.query_one(f"#{pane_id}")
        if hasattr(pane, "get_export_text"):
            value = pane.get_export_text()
            if isinstance(value, str):
                return pane_id, value
        return pane_id, str(pane)

    def _export_dir(self) -> Path:
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache_home:
            return Path(xdg_cache_home).expanduser() / "toktrail" / "exports"
        return Path.home() / ".cache" / "toktrail" / "exports"

    def _copy_fallback_path(self) -> Path:
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache_home:
            return Path(xdg_cache_home).expanduser() / "toktrail" / "tui-last-view.txt"
        return Path.home() / ".cache" / "toktrail" / "tui-last-view.txt"

    def _copy_to_clipboard(self, text: str) -> bool:
        if self._run_clipboard(["termux-clipboard-set"], text):
            return True
        wayland = os.environ.get("WAYLAND_DISPLAY")
        display = os.environ.get("DISPLAY")
        if wayland and self._run_clipboard(["wl-copy"], text):
            return True
        if display and self._run_clipboard(["xclip", "-selection", "clipboard"], text):
            return True
        if display and self._run_clipboard(["xsel", "--clipboard", "--input"], text):
            return True
        if self._run_clipboard(["pbcopy"], text):
            return True
        if self._run_clipboard(["clip.exe"], text):
            return True
        return self._run_clipboard(
            ["powershell.exe", "-NoProfile", "-Command", "Set-Clipboard"],
            text,
        )

    def _resolve_display(self) -> TuiDisplay:
        size = getattr(self, "size", None)
        columns = getattr(size, "width", None)
        rows = getattr(size, "height", None)
        return resolve_tui_display(self.state.tui_mode, columns=columns, rows=rows)

    def _apply_display(self) -> None:
        display = self._resolve_display()
        if self._tui_display is not None and self._tui_display.mode != display.mode:
            self._compact_details_visible = False
        self._tui_display = display
        self.set_class(display.mode == "compact", "compact")
        self.set_class(display.mode == "micro", "micro")
        hide_compact_details = display.mode == "compact" and (
            not self._compact_details_visible
        )
        self.set_class(hide_compact_details, "compact-details-hidden")
        for pane in (
            self._dashboard,
            self._sessions,
            self._areas,
            self._prices,
            self._subscriptions,
            self._config,
        ):
            if hasattr(pane, "set_display"):
                pane.set_display(display)

    def _current_pane_id(self) -> str:
        content = self.query_one("#content", ContentSwitcher)
        return content.current or "dashboard"

    def _focus_table_if_needed(self, pane_id: str) -> None:
        table_id = {
            "sessions": "#sessions-table",
            "areas": "#areas-table",
        }.get(pane_id)
        if table_id is None:
            return
        try:
            table = self.query_one(table_id)
        except Exception:
            return
        table.focus()

    def _date_label(self) -> str:
        if self._date_offset == 0:
            return "today"
        from datetime import datetime, timedelta

        tz = resolve_timezone(
            timezone_name=self.state.timezone_name, utc=self.state.utc
        )
        now = datetime.now(tz)
        target = now - timedelta(days=self._date_offset)
        return target.strftime("%Y-%m-%d")

    def _update_status_with_date(self) -> None:
        pane_id = self._current_pane_id()
        if pane_id not in ("sessions", "areas"):
            return
        label = self._date_label()
        cap = pane_id.capitalize()
        self._status.update(f"{cap}: {label}")

    def action_day_back(self) -> None:
        pane_id = self._current_pane_id()
        if pane_id not in ("sessions", "areas"):
            return
        self._date_offset += 1
        self._refresh_views()

    def action_day_forward(self) -> None:
        pane_id = self._current_pane_id()
        if pane_id not in ("sessions", "areas"):
            return
        if self._date_offset <= 0:
            return
        self._date_offset -= 1
        self._refresh_views()

    def _update_compact_bar(self) -> None:
        display = self._tui_display or self._resolve_display()
        if display.mode == "full":
            self._compact_bar.update("")
            return
        current = self._current_pane_id()
        labels = {
            "dashboard": "Dash",
            "sessions": "Sess",
            "areas": "Area",
            "prices": "Price",
            "subscriptions": "Subs",
            "config": "Cfg",
        }
        date_info = ""
        if current in ("sessions", "areas") and self._date_offset > 0:
            date_info = f"  {self._date_label()}"
        if display.mode == "micro":
            self._compact_bar.update(
                f"toktrail {labels.get(current, current)}{date_info}  ? help  q quit"
            )
            return
        self._compact_bar.update(
            "toktrail [1]Dash [2]Sess [3]Area [4]Price [5]Subs [6]Cfg"
            f"{date_info}  r refresh  ? help"
        )

    def _micro_detail_preview(self) -> str:
        content = self.query_one("#content", ContentSwitcher)
        pane_id = content.current or "dashboard"
        detail_id = {
            "sessions": "#sessions-detail",
            "areas": "#areas-detail",
            "prices": "#prices-detail",
            "subscriptions": "#subscriptions-detail",
        }.get(pane_id)
        if detail_id is None:
            return "No details for this pane."
        try:
            detail = self.query_one(detail_id, Static)
        except Exception:
            return "No details available."
        text = detail.renderable
        value = str(text)
        first_line = value.splitlines()[0] if value else "No details available."
        return first_line

    def _run_clipboard(self, command: list[str], text: str) -> bool:
        executable = command[0]
        if shutil.which(executable) is None:
            return False
        try:
            subprocess.run(
                command,
                input=text,
                text=True,
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, OSError):
            return False
        return True
