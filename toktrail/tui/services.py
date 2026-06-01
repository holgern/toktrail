# mypy: ignore-errors
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal

from toktrail.api.analysis import session_digest as session_digest_api
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
    SubscriptionUsageRow,
    UnconfiguredModelRow,
    UsageSeriesBucket,
    UsageSessionRow,
)
from toktrail.api.prices import (
    list_prices,
    list_unconfigured_models,
    upsert_manual_price,
)
from toktrail.api.reports import (
    subscription_usage_report,
    usage_areas_report,
    usage_report,
    usage_series_report,
    usage_sessions_report,
)
from toktrail.db import connect as connect_db
from toktrail.db import get_source_session_digest
from toktrail.errors import InvalidAPIUsageError
from toktrail.periods import resolve_timezone
from toktrail.tui.session_detail import render_session_health_text
from toktrail.tui.state import ToktrailTuiState


@dataclass(frozen=True)
class DashboardData:
    view: str
    title: str
    total_tokens: int
    actual_cost_usd: float
    virtual_cost_usd: float
    savings_usd: float
    active_area: str | None
    unpriced_count: int
    top_providers: tuple[ProviderSummaryRow, ...]
    top_models: tuple[ModelSummaryRow, ...]
    series_buckets: tuple[UsageSeriesBucket, ...]
    config_path: str
    prices_path: str
    subscriptions_path: str


DashboardView = Literal["today", "daily", "weekly"]


@dataclass(frozen=True)
class SessionDigestData:
    summary: str
    health_label: str
    health_detail: str
    tool_failure_count: int
    signal_summary: str
    penalties: tuple[str, ...]


@dataclass(frozen=True)
class SessionsData:
    sessions: tuple[UsageSessionRow, ...]
    digests: dict[str, SessionDigestData]


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


@dataclass(frozen=True)
class SubscriptionsData:
    generated_at_ms: int
    subscriptions: tuple[SubscriptionUsageRow, ...]


class ToktrailTuiService:
    def __init__(self, state: ToktrailTuiState) -> None:
        self.state = state

    def dashboard(self, view: DashboardView = "today") -> DashboardData:
        active = get_active_area_status(self.state.db_path).area
        summary = config_summary(self.state.config_path)
        if view == "today":
            report = usage_report(
                self.state.db_path,
                period="today",
                timezone=self.state.timezone_name,
                utc=self.state.utc,
                config_path=self.state.config_path,
            )
            return DashboardData(
                view=view,
                title="Today",
                total_tokens=report.totals.tokens.total,
                actual_cost_usd=float(report.totals.costs.actual_cost_usd),
                virtual_cost_usd=float(report.totals.costs.virtual_cost_usd),
                savings_usd=float(report.totals.costs.savings_usd),
                active_area=None if active is None else active.path,
                unpriced_count=report.totals.costs.unpriced_count,
                top_providers=tuple(report.by_provider[:3]),
                top_models=tuple(report.by_model[:3]),
                series_buckets=(),
                config_path=str(summary["config_path"]),
                prices_path=str(summary["manual_prices_path"]),
                subscriptions_path=str(summary["subscriptions_path"]),
            )
        granularity = "daily" if view == "daily" else "weekly"
        title = "Daily" if view == "daily" else "Weekly"
        series = usage_series_report(
            self.state.db_path,
            granularity=granularity,
            timezone=self.state.timezone_name,
            utc=self.state.utc,
            config_path=self.state.config_path,
        )
        limit = 14 if view == "daily" else 12
        return DashboardData(
            view=view,
            title=title,
            total_tokens=series.totals.tokens.total,
            actual_cost_usd=float(series.totals.costs.actual_cost_usd),
            virtual_cost_usd=float(series.totals.costs.virtual_cost_usd),
            savings_usd=float(series.totals.costs.savings_usd),
            active_area=None if active is None else active.path,
            unpriced_count=series.totals.costs.unpriced_count,
            top_providers=(),
            top_models=(),
            series_buckets=tuple(series.buckets[:limit]),
            config_path=str(summary["config_path"]),
            prices_path=str(summary["manual_prices_path"]),
            subscriptions_path=str(summary["subscriptions_path"]),
        )

    def _day_bounds(self, date_offset: int) -> tuple[int, int]:
        """Return (since_ms, until_ms) for a day offset from today."""
        tz = resolve_timezone(
            timezone_name=self.state.timezone_name, utc=self.state.utc
        )
        from datetime import datetime

        now = datetime.now(tz)
        target = (now - timedelta(days=date_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = target + timedelta(days=1)
        return int(target.timestamp() * 1000), int(end.timestamp() * 1000)

    def sessions(self, date_offset: int = 0) -> SessionsData:
        if date_offset == 0:
            report = usage_sessions_report(
                self.state.db_path,
                period="today",
                timezone=self.state.timezone_name,
                utc=self.state.utc,
                config_path=self.state.config_path,
                limit=50,
            )
        else:
            since_ms, until_ms = self._day_bounds(date_offset)
            report = usage_sessions_report(
                self.state.db_path,
                timezone=self.state.timezone_name,
                utc=self.state.utc,
                since_ms=since_ms,
                until_ms=until_ms,
                config_path=self.state.config_path,
                limit=50,
            )
        return SessionsData(
            sessions=tuple(report.sessions),
            digests=self._session_digest_data(tuple(report.sessions)),
        )

    def session_detail_text(self, session_key: str) -> str:
        digest = session_digest_api(
            db_path=self.state.db_path,
            config_path=self.state.config_path,
            session_key=session_key,
            refresh=False,
            persist=False,
        )
        return render_session_health_text(
            session_key,
            digest,
            utc=self.state.utc,
            rich_output=True,
        )

    def _session_digest_data(
        self, sessions: tuple[UsageSessionRow, ...]
    ) -> dict[str, SessionDigestData]:
        if not sessions:
            return {}
        conn = connect_db(self.state.db_path)
        try:
            digests: dict[str, SessionDigestData] = {}
            for session in sessions:
                if session.origin_machine_id is None:
                    continue
                digest = get_source_session_digest(
                    conn,
                    origin_machine_id=session.origin_machine_id,
                    harness=session.harness,
                    source_session_id=session.source_session_id,
                )
                if digest is None:
                    continue
                health = digest.health
                if health is None:
                    health_label = "-"
                    health_detail = "- unknown (low)"
                    signal_summary = "retry=0 edit=0 streak=0"
                    penalties: tuple[str, ...] = ()
                else:
                    health_label = (
                        f"{health.grade}{health.score}"
                        if health.grade and health.score is not None
                        else health.outcome
                    )
                    score = "-" if health.score is None else str(health.score)
                    grade = health.grade or "-"
                    health_detail = (
                        f"{grade} {score} {health.outcome} "
                        f"({health.outcome_confidence})"
                    )
                    signal_summary = (
                        f"retry={health.retry_count} "
                        f"edit={health.edit_churn_count} "
                        f"streak={health.consecutive_failure_max}"
                    )
                    penalties = tuple(
                        f"{penalty.kind} -{penalty.points}"
                        for penalty in health.penalties
                    )
                digests[session.key] = SessionDigestData(
                    summary=digest.summary.one_line or "-",
                    health_label=health_label,
                    health_detail=health_detail,
                    tool_failure_count=digest.tool_health.tool_failure_count,
                    signal_summary=signal_summary,
                    penalties=penalties,
                )
            return digests
        finally:
            conn.close()

    def areas(self, date_offset: int = 0) -> AreasData:
        status = get_active_area_status(self.state.db_path)
        areas = list_areas(self.state.db_path)
        if date_offset == 0:
            usage = usage_areas_report(
                self.state.db_path,
                period="today",
                timezone=self.state.timezone_name,
                utc=self.state.utc,
                config_path=self.state.config_path,
            )
        else:
            since_ms, until_ms = self._day_bounds(date_offset)
            usage = usage_areas_report(
                self.state.db_path,
                timezone=self.state.timezone_name,
                utc=self.state.utc,
                since_ms=since_ms,
                until_ms=until_ms,
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

    def subscriptions(self) -> SubscriptionsData:
        report = subscription_usage_report(
            self.state.db_path,
            config_path=self.state.config_path,
            prices_path=self.state.prices_path,
            prices_dir=self.state.prices_dir,
            subscriptions_path=self.state.subscriptions_path,
        )
        return SubscriptionsData(
            generated_at_ms=report.generated_at_ms,
            subscriptions=tuple(report.subscriptions),
        )

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
