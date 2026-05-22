# mypy: ignore-errors
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import ContentSwitcher, Footer, Header, Static, Tab, Tabs

from toktrail.errors import InvalidAPIUsageError
from toktrail.tui.panes.areas import AreasPane
from toktrail.tui.panes.config import ConfigPane
from toktrail.tui.panes.dashboard import DashboardPane
from toktrail.tui.panes.prices import PricesPane
from toktrail.tui.panes.sessions import SessionsPane
from toktrail.tui.screens.area_form import AreaFormScreen
from toktrail.tui.screens.confirm import ConfirmScreen
from toktrail.tui.screens.price_form import PriceFormScreen
from toktrail.tui.services import ToktrailTuiService
from toktrail.tui.state import ToktrailTuiState


class ToktrailTuiApp(App[None]):
    TITLE = "toktrail"
    CSS_PATH = "styles/toktrail.tcss"

    BINDINGS = [
        Binding("1", "switch_pane('dashboard')", "Dashboard", show=False),
        Binding("2", "switch_pane('sessions')", "Sessions", show=False),
        Binding("3", "switch_pane('areas')", "Areas", show=False),
        Binding("4", "switch_pane('prices')", "Prices", show=False),
        Binding("5", "switch_pane('config')", "Config", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "create_area", "Create area"),
        Binding("u", "set_active_area", "Use area"),
        Binding("c", "clear_active_area", "Clear area"),
        Binding("l", "assign_latest", "Assign latest"),
        Binding("s", "assign_selected_session", "Assign selected"),
        Binding("e", "edit_selected_price", "Edit price"),
        Binding("question_mark", "help", "Help"),
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
        )
        self.service = ToktrailTuiService(self.state)
        self._dashboard = DashboardPane(id="dashboard")
        self._sessions = SessionsPane(id="sessions")
        self._areas = AreasPane(id="areas")
        self._prices = PricesPane(id="prices")
        self._config = ConfigPane(id="config")
        self._status = Static("", id="status")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(
            Tab("Dashboard", id="tab-dashboard"),
            Tab("Sessions", id="tab-sessions"),
            Tab("Areas", id="tab-areas"),
            Tab("Prices", id="tab-prices"),
            Tab("Config", id="tab-config"),
            id="tabs",
        )
        yield ContentSwitcher(
            self._dashboard,
            self._sessions,
            self._areas,
            self._prices,
            self._config,
            initial="dashboard",
            id="content",
        )
        yield self._status
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_views()
        if self.state.initial_area:
            self.service.set_active_area(self.state.initial_area)
            self._refresh_views()
        if self.state.refresh_on_start:
            self.action_refresh()

    def action_switch_pane(self, pane_id: str) -> None:
        content = self.query_one("#content", ContentSwitcher)
        content.current = pane_id
        tabs = self.query_one("#tabs", Tabs)
        tabs.active = f"tab-{pane_id}"

    def action_help(self) -> None:
        self._status.update(
            "1-5 switch panes, r refresh, q quit, "
            "areas: create/set/clear/assign latest/assign selected, prices: edit"
        )

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
        self._dashboard.set_data(self.service.dashboard())
        self._sessions.set_data(self.service.sessions())
        self._areas.set_data(self.service.areas())
        self._prices.set_data(self.service.prices())
        self._config.set_data(self.service.config())
