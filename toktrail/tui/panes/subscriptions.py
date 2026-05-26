# mypy: ignore-errors
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from toktrail.api.models import (
    SubscriptionBillingPeriod,
    SubscriptionUsagePeriod,
    SubscriptionUsageRow,
)
from toktrail.cli_parts.formatting import _format_cost, _format_int, _format_percent
from toktrail.formatting import format_duration_seconds, format_epoch_ms_compact
from toktrail.tui.layout import TuiDisplay, resolve_tui_display
from toktrail.tui.panes.exportable import ExportablePaneMixin
from toktrail.tui.services import SubscriptionsData


@dataclass(frozen=True)
class _SubscriptionWindowView:
    key: str
    subscription: SubscriptionUsageRow
    period: SubscriptionUsagePeriod


def _format_left(period: SubscriptionUsagePeriod) -> str:
    left = _format_cost(period.remaining_usd)
    if period.over_limit_usd > 0:
        return f"{left} over {_format_cost(period.over_limit_usd)}"
    return left


def _format_reset(period: SubscriptionUsagePeriod, *, now_ms: int) -> str:
    if period.until_ms is not None:
        seconds = max(0, (period.until_ms - now_ms) // 1000)
        return f"in {format_duration_seconds(seconds)}"
    if period.status == "waiting_for_first_use":
        return "on first use"
    if period.status == "expired_waiting_for_next_use":
        return "on next use"
    return "-"


def _format_status(status: str) -> str:
    return {
        "waiting_for_first_use": "waiting",
        "expired_waiting_for_next_use": "expired",
    }.get(status, status)


def _format_break_even(billing: SubscriptionBillingPeriod) -> str:
    pct = billing.break_even_percent
    if pct is not None and pct >= Decimal("100"):
        return f"reached ({_format_percent(pct)})"
    if pct is not None:
        return f"{_format_percent(pct)}"
    return "-"


class SubscriptionsPane(ExportablePaneMixin, Vertical):
    selected_window: _SubscriptionWindowView | None = None

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.tui_display: TuiDisplay = resolve_tui_display("full")
        self._rows_by_key: dict[str, _SubscriptionWindowView] = {}

    def set_display(self, display: TuiDisplay) -> None:
        self.tui_display = display

    def compose(self) -> ComposeResult:
        table = DataTable(id="subscriptions-table")
        table.cursor_type = "row"
        yield table
        yield Static("No subscription selected.", id="subscriptions-detail")

    def set_data(self, data: SubscriptionsData) -> None:
        table = self.query_one("#subscriptions-table", DataTable)
        detail = self.query_one("#subscriptions-detail", Static)
        self._configure_columns(table)
        table.clear(columns=False)
        self._rows_by_key = {}

        if not data.subscriptions:
            self.selected_window = None
            detail.update("No provider subscriptions configured.")
            self.export_text = "No provider subscriptions configured."
            return

        previous_key = (
            None if self.selected_window is None else self.selected_window.key
        )
        first_key: str | None = None
        now_ms = int(time.time() * 1000)

        for subscription in data.subscriptions:
            for index, period in enumerate(subscription.periods):
                key = f"{subscription.subscription_id}:{period.period}:{index}"
                view = _SubscriptionWindowView(key, subscription, period)
                self._rows_by_key[key] = view
                if first_key is None:
                    first_key = key
                self._add_table_row(table, view, now_ms=now_ms)

        selected_key = previous_key if previous_key in self._rows_by_key else first_key
        if selected_key is None:
            self.selected_window = None
            detail.update("No active subscription windows.")
        else:
            self.selected_window = self._rows_by_key[selected_key]
            table.move_cursor(row=table.get_row_index(selected_key), column=0)
            self._update_detail(self.selected_window, now_ms=now_ms)

        self.export_text = self._build_export_text(data, now_ms=now_ms)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "subscriptions-table":
            return
        key = str(event.row_key.value)
        view = self._rows_by_key.get(key)
        if view is None:
            return
        self.selected_window = view
        now_ms = int(time.time() * 1000)
        self._update_detail(view, now_ms=now_ms)

    def _configure_columns(self, table: DataTable) -> None:
        table.clear(columns=True)
        mode = self.tui_display.mode
        if mode == "micro":
            table.add_columns("Plan", "Period", "Used", "Left")
        elif mode == "compact":
            table.add_columns("Plan", "Period", "Status", "Used", "Left", "Reset")
        else:
            table.add_columns(
                "Plan",
                "Providers",
                "Period",
                "Status",
                "Used",
                "Limit",
                "Left",
                "Used%",
                "Reset",
                "Msgs",
                "Tokens",
            )

    def _add_table_row(
        self,
        table: DataTable,
        view: _SubscriptionWindowView,
        *,
        now_ms: int,
    ) -> None:
        sub = view.subscription
        period = view.period
        plan = sub.display_name
        period_label = period.period
        used = _format_cost(period.used_usd)
        left = _format_left(period)

        mode = self.tui_display.mode
        if mode == "micro":
            table.add_row(plan, period_label, used, left, key=view.key)
        elif mode == "compact":
            table.add_row(
                plan,
                period_label,
                _format_status(period.status),
                used,
                left,
                _format_reset(period, now_ms=now_ms),
                key=view.key,
            )
        else:
            table.add_row(
                plan,
                ",".join(sub.usage_provider_ids),
                period_label,
                _format_status(period.status),
                used,
                _format_cost(period.limit_usd),
                left,
                _format_percent(period.percent_used),
                _format_reset(period, now_ms=now_ms),
                _format_int(period.message_count),
                _format_int(period.tokens.total),
                key=view.key,
            )

    def _update_detail(self, view: _SubscriptionWindowView, *, now_ms: int) -> None:
        detail = self.query_one("#subscriptions-detail", Static)
        sub = view.subscription
        period = view.period
        lines = [
            f"Subscription: {sub.display_name} ({sub.subscription_id})",
            f"Providers: {', '.join(sub.usage_provider_ids)}",
            f"Quota basis: {sub.quota_cost_basis}",
            f"Timezone: {sub.timezone or '-'}",
            f"Period: {period.period} ({_format_status(period.status)})",
        ]
        if period.until_ms is not None:
            seconds = max(0, (period.until_ms - now_ms) // 1000)
            lines.append(
                "Resets in: " + format_duration_seconds(seconds, compact=False)
            )
        if period.since_ms is not None:
            lines.append(f"Window start: {format_epoch_ms_compact(period.since_ms)}")
        if period.until_ms is not None:
            lines.append(f"Window end: {format_epoch_ms_compact(period.until_ms)}")
        lines.append(
            f"Used: {_format_cost(period.used_usd)} / {_format_cost(period.limit_usd)}"
        )
        lines.append(f"Remaining: {_format_left(period)}")
        if period.percent_used is not None:
            lines.append(f"Used %: {_format_percent(period.percent_used)}")
        lines.append(f"Messages: {_format_int(period.message_count)}")
        lines.append(f"Tokens: {_format_int(period.tokens.total)}")

        if sub.billing is not None:
            billing = sub.billing
            lines.append("")
            lines.append("Billing")
            lines.append(f"  Period: {billing.period}")
            lines.append(f"  Fixed cost: {_format_cost(billing.fixed_cost_usd)}")
            lines.append(f"  Current value: {_format_cost(billing.value_usd)}")
            lines.append(f"  Net savings: {_format_cost(billing.net_savings_usd)}")
            lines.append(f"  Break-even: {_format_break_even(billing)}")

        if period.warnings:
            lines.append("")
            lines.append("Warnings")
            for warning in period.warnings:
                kind = warning.get("kind", "unknown")
                if kind == "zero_cost_with_tokens":
                    basis = warning.get("basis", "?")
                    msgs = warning.get("message_count", "?")
                    lines.append(
                        f"  provider/model has {msgs} messages"
                        f" but zero cost for basis={basis}"
                    )

                else:
                    lines.append(f"  {kind}: {warning}")

        detail.update("\n".join(lines))

    def _build_export_text(self, data: SubscriptionsData, *, now_ms: int) -> str:
        lines: list[str] = ["Subscriptions"]
        lines.append(
            "plan\tid\tproviders\tbasis\ttimezone\tperiod\tstatus\tused\tlimit\tleft\tused_pct\tmsgs\ttokens\treset"
        )
        for sub in data.subscriptions:
            for period in sub.periods:
                lines.append(
                    "\t".join(
                        [
                            sub.display_name,
                            sub.subscription_id,
                            ",".join(sub.usage_provider_ids),
                            sub.quota_cost_basis,
                            sub.timezone or "-",
                            period.period,
                            period.status,
                            _format_cost(period.used_usd),
                            _format_cost(period.limit_usd),
                            _format_left(period),
                            _format_percent(period.percent_used),
                            str(period.message_count),
                            str(period.tokens.total),
                            _format_reset(period, now_ms=now_ms),
                        ]
                    )
                )

        has_billing = any(sub.billing is not None for sub in data.subscriptions)
        if has_billing:
            lines.append("")
            lines.append("Billing")
            lines.append("plan\tperiod\tfixed\tvalue\tnet_savings\tbreak_even")
            for sub in data.subscriptions:
                if sub.billing is not None:
                    billing = sub.billing
                    lines.append(
                        "\t".join(
                            [
                                sub.display_name,
                                billing.period,
                                _format_cost(billing.fixed_cost_usd),
                                _format_cost(billing.value_usd),
                                _format_cost(billing.net_savings_usd),
                                _format_break_even(billing),
                            ]
                        )
                    )

        return "\n".join(lines)
