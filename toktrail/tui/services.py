# mypy: ignore-errors
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from toktrail.api.areas import (
    assign_area_to_session,
    assign_area_to_session_key,
    clear_active_area,
    create_area,
    get_active_area_status,
    list_areas,
    set_active_area,
)
from toktrail.api.config import config_summary
from toktrail.api.imports import import_configured_usage
from toktrail.api.models import (
    AreaSummaryRow,
    ModelSummaryRow,
    PriceRow,
    ProviderSummaryRow,
    UnconfiguredModelRow,
    UsageSessionRow,
)
from toktrail.api.prices import (
    list_prices,
    list_unconfigured_models,
    upsert_manual_price,
)
from toktrail.api.reports import usage_areas_report, usage_report, usage_sessions_report
from toktrail.errors import InvalidAPIUsageError
from toktrail.tui.state import ToktrailTuiState


@dataclass(frozen=True)
class DashboardData:
    total_tokens: int
    actual_cost_usd: float
    virtual_cost_usd: float
    savings_usd: float
    active_area: str | None
    unpriced_count: int
    top_providers: tuple[ProviderSummaryRow, ...]
    top_models: tuple[ModelSummaryRow, ...]
    config_path: str
    prices_path: str
    subscriptions_path: str


@dataclass(frozen=True)
class SessionsData:
    sessions: tuple[UsageSessionRow, ...]


@dataclass(frozen=True)
class AreasData:
    area_paths: tuple[str, ...]
    active_area: str | None
    usage_rows: tuple[AreaSummaryRow, ...]


@dataclass(frozen=True)
class PricesData:
    rows: tuple[PriceRow, ...]
    unconfigured: tuple[UnconfiguredModelRow, ...]


@dataclass(frozen=True)
class ConfigData:
    summary: dict[str, object]


class ToktrailTuiService:
    def __init__(self, state: ToktrailTuiState) -> None:
        self.state = state

    def dashboard(self) -> DashboardData:
        report = usage_report(
            self.state.db_path,
            period="today",
            timezone=self.state.timezone_name,
            utc=self.state.utc,
            config_path=self.state.config_path,
        )
        active = get_active_area_status(self.state.db_path).area
        summary = config_summary(self.state.config_path)
        return DashboardData(
            total_tokens=report.totals.tokens.total,
            actual_cost_usd=float(report.totals.costs.actual_cost_usd),
            virtual_cost_usd=float(report.totals.costs.virtual_cost_usd),
            savings_usd=float(report.totals.costs.savings_usd),
            active_area=None if active is None else active.path,
            unpriced_count=report.totals.costs.unpriced_count,
            top_providers=tuple(report.by_provider[:3]),
            top_models=tuple(report.by_model[:3]),
            config_path=str(summary["config_path"]),
            prices_path=str(summary["manual_prices_path"]),
            subscriptions_path=str(summary["subscriptions_path"]),
        )

    def sessions(self) -> SessionsData:
        report = usage_sessions_report(
            self.state.db_path,
            period="today",
            timezone=self.state.timezone_name,
            utc=self.state.utc,
            config_path=self.state.config_path,
            limit=50,
        )
        return SessionsData(sessions=tuple(report.sessions))

    def areas(self) -> AreasData:
        status = get_active_area_status(self.state.db_path)
        areas = list_areas(self.state.db_path)
        usage = usage_areas_report(
            self.state.db_path,
            period="today",
            timezone=self.state.timezone_name,
            utc=self.state.utc,
            config_path=self.state.config_path,
        )
        return AreasData(
            area_paths=tuple(area.path for area in areas),
            active_area=None if status.area is None else status.area.path,
            usage_rows=tuple(usage.areas),
        )

    def prices(self) -> PricesData:
        return PricesData(
            rows=list_prices(
                config_path=self.state.config_path,
                prices_path=self.state.prices_path,
                prices_dir=self.state.prices_dir,
                include_provider_prices=True,
            ),
            unconfigured=list_unconfigured_models(
                self.state.db_path,
                period="today",
                config_path=self.state.config_path,
            ),
        )

    def config(self) -> ConfigData:
        return ConfigData(summary=config_summary(self.state.config_path))

    def refresh(self) -> tuple[object, ...]:
        return tuple(
            import_configured_usage(
                self.state.db_path,
                config_path=self.state.config_path,
                use_active_session=False,
                refresh_mode="full",
            )
        )

    def create_area(self, path: str) -> None:
        create_area(path, db_path=self.state.db_path)

    def set_active_area(self, path: str) -> None:
        set_active_area(path, db_path=self.state.db_path, create=False)

    def clear_active_area(self) -> None:
        clear_active_area(db_path=self.state.db_path)

    def assign_latest_session_to_area(self, path: str) -> None:
        report = usage_sessions_report(
            self.state.db_path,
            period="today",
            timezone=self.state.timezone_name,
            utc=self.state.utc,
            config_path=self.state.config_path,
            limit=1,
        )
        if not report.sessions:
            raise InvalidAPIUsageError("No source session available to assign.")
        session = report.sessions[0]
        if session.key:
            assign_area_to_session_key(path, session.key, db_path=self.state.db_path)
            return
        assign_area_to_session(
            path,
            harness=session.harness,
            source_session_id=session.source_session_id,
            machine=session.origin_machine_id,
            db_path=self.state.db_path,
        )

    def assign_session_to_area(self, path: str, session_key: str) -> None:
        assign_area_to_session_key(path, session_key, db_path=self.state.db_path)

    def upsert_manual_price(self, row: PriceRow) -> Path:
        return upsert_manual_price(
            row,
            prices_path=self.state.prices_path,
            config_path=self.state.config_path,
            create_missing=True,
        )
