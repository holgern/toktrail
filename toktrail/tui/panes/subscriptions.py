# mypy: ignore-errors
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable

from toktrail.api.models import (
    SubscriptionBillingPeriod,
    SubscriptionUsagePeriod,
    SubscriptionUsageRow,
)
from toktrail.cli_parts.formatting import _format_cost, _format_int, _format_percent
from toktrail.formatting import format_duration_seconds, format_epoch_ms_compact
from toktrail.tui.layout import TuiDisplay
from toktrail.tui.panes.exportable import ExportablePaneMixin
from toktrail.tui.panes.table import move_table_to_key, restore_selected_key
from toktrail.tui.services import SubscriptionsData


@dataclass(frozen=True)
class _SubscriptionListView:
    key: str
    subscription: SubscriptionUsageRow


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


def _primary_period(
    subscription: SubscriptionUsageRow,
) -> SubscriptionUsagePeriod | None:
    active = [period for period in subscription.periods if period.status == "active"]
    if active:
        return active[0]
    if subscription.periods:
        return subscription.periods[0]
    return None


def render_subscription_detail(
    subscription: SubscriptionUsageRow,
    *,
    now_ms: int,
) -> str:
    scope_label = (
        subscription.scope.label if subscription.scope is not None else "all areas"
    )
    lines = [
        f"Subscription: {subscription.display_name} ({subscription.subscription_id})",
        f"Providers: {', '.join(subscription.usage_provider_ids)}",
        f"Scope: {scope_label}",
        f"Quota basis: {subscription.quota_cost_basis}",
        f"Timezone: {subscription.timezone or '-'}",
    ]

    if subscription.billing is not None:
        billing = subscription.billing
        lines.extend(
            [
                "",
                "Billing",
                f"  Period: {billing.period}",
                f"  Fixed cost: {_format_cost(billing.fixed_cost_usd)}",
                f"  Current value: {_format_cost(billing.value_usd)}",
                f"  Net savings: {_format_cost(billing.net_savings_usd)}",
                f"  Break-even: {_format_break_even(billing)}",
            ]
        )

    lines.extend(["", "Quota windows"])
    for period in subscription.periods:
        lines.append("")
        lines.append(f"  {period.period} ({_format_status(period.status)})")
        if period.until_ms is not None:
            seconds = max(0, (period.until_ms - now_ms) // 1000)
            lines.append(
                "    Resets in: " + format_duration_seconds(seconds, compact=False)
            )
        if period.since_ms is not None:
            lines.append(
                f"    Window start: {format_epoch_ms_compact(period.since_ms)}"
            )
        if period.until_ms is not None:
            lines.append(f"    Window end: {format_epoch_ms_compact(period.until_ms)}")
        used_line = (
            f"    Used: {_format_cost(period.used_usd)} / "
            f"{_format_cost(period.limit_usd)}"
        )
        lines.append(used_line)
        lines.append(f"    Remaining: {_format_left(period)}")
        if period.percent_used is not None:
            lines.append(f"    Used %: {_format_percent(period.percent_used)}")
        lines.append(f"    Messages: {_format_int(period.message_count)}")
        lines.append(f"    Tokens: {_format_int(period.tokens.total)}")

        if period.warnings:
            lines.append("    Warnings:")
            for warning in period.warnings:
                kind = warning.get("kind", "unknown")
                if kind == "zero_cost_with_tokens":
                    basis = warning.get("basis", "?")
                    msgs = warning.get("message_count", "?")
                    lines.append(
                        "      provider/model has "
                        f"{msgs} messages but zero cost for basis={basis}"
                    )
                else:
                    lines.append(f"      {kind}: {warning}")

    if not subscription.periods:
        lines.append("  No quota windows configured.")

    return "\n".join(lines)


class SubscriptionsPane(ExportablePaneMixin, Vertical):
    selected_subscription: SubscriptionUsageRow | None = None

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.tui_display: TuiDisplay = TuiDisplay(mode="full", columns=80, rows=24)
        self._rows_by_key: dict[str, _SubscriptionListView] = {}

    def set_display(self, display: TuiDisplay) -> None:
        self.tui_display = display

    def compose(self) -> ComposeResult:
        table = DataTable(id="subscriptions-table")
        table.cursor_type = "row"
        yield table

    def set_data(self, data: SubscriptionsData) -> None:
        table = self.query_one("#subscriptions-table", DataTable)
        self._configure_columns(table)
        table.clear(columns=False)
        self._rows_by_key = {}

        if not data.subscriptions:
            self.selected_subscription = None
            self.export_text = "No provider subscriptions configured."
            return

        previous_key = (
            None
            if self.selected_subscription is None
            else self.selected_subscription.subscription_id
        )
        now_ms = int(time.time() * 1000)

        for subscription in data.subscriptions:
            key = subscription.subscription_id
            view = _SubscriptionListView(key=key, subscription=subscription)
            self._rows_by_key[key] = view
            self._add_subscription_row(table, view, now_ms=now_ms)

        selected_key = restore_selected_key(previous_key, self._rows_by_key.keys())
        if selected_key is None:
            self.selected_subscription = None
        else:
            self.selected_subscription = self._rows_by_key[selected_key].subscription
            move_table_to_key(table, selected_key)

        self.export_text = self._build_export_text(data, now_ms=now_ms)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "subscriptions-table":
            return
        key = str(event.row_key.value)
        view = self._rows_by_key.get(key)
        if view is None:
            return
        self.selected_subscription = view.subscription

    def _configure_columns(self, table: DataTable) -> None:
        table.clear(columns=True)
        mode = self.tui_display.mode
        if mode == "micro":
            table.add_columns("Plan", "Used", "Left")
        elif mode == "compact":
            table.add_columns("Plan", "Used", "Left", "Reset")
        else:
            table.add_columns(
                "Plan",
                "Providers",
                "Scope",
                "Basis",
                "Used",
                "Limit",
                "Left",
                "Reset",
                "Windows",
            )

    def _add_subscription_row(
        self,
        table: DataTable,
        view: _SubscriptionListView,
        *,
        now_ms: int,
    ) -> None:
        sub = view.subscription
        primary = _primary_period(sub)
        scope_label = sub.scope.label if sub.scope is not None else "all areas"

        used = "-" if primary is None else _format_cost(primary.used_usd)
        limit = "-" if primary is None else _format_cost(primary.limit_usd)
        left = "-" if primary is None else _format_left(primary)
        reset = "-" if primary is None else _format_reset(primary, now_ms=now_ms)

        mode = self.tui_display.mode
        if mode == "micro":
            table.add_row(sub.display_name, used, left, key=view.key)
        elif mode == "compact":
            table.add_row(
                sub.display_name,
                used,
                left,
                reset,
                key=view.key,
            )
        else:
            table.add_row(
                sub.display_name,
                ",".join(sub.usage_provider_ids),
                scope_label,
                sub.quota_cost_basis,
                used,
                limit,
                left,
                reset,
                str(len(sub.periods)),
                key=view.key,
            )

    def _build_export_text(self, data: SubscriptionsData, *, now_ms: int) -> str:
        lines: list[str] = ["Subscriptions"]
        lines.append(
            "plan\tid\tproviders\tscope\tbasis\ttimezone\tperiod\tstatus\tused\tlimit\tleft\tused_pct\tmsgs\ttokens\treset"
        )
        for sub in data.subscriptions:
            scope_label = sub.scope.label if sub.scope is not None else "all areas"
            for period in sub.periods:
                lines.append(
                    "\t".join(
                        [
                            sub.display_name,
                            sub.subscription_id,
                            ",".join(sub.usage_provider_ids),
                            scope_label,
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
