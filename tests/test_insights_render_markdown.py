"""Tests for the Markdown renderer."""

from __future__ import annotations

from decimal import Decimal

from toktrail.insights.models import (
    DeterministicSuggestion,
    InsightAggregate,
    InsightAnomaly,
    InsightDelta,
    InsightGroupRow,
    InsightSessionMeta,
    InsightsReport,
)
from toktrail.insights.render_markdown import render_insights_markdown


def _make_session(**kwargs: object) -> InsightSessionMeta:
    from decimal import Decimal as D

    defaults = dict(
        harness="codex",
        source_session_id="sess-001",
        origin_machine_id="machine-1",
        area_path=None,
        start_ms=1700000000000,
        end_ms=1700003600000,
        duration_ms=3600000,
        total_tokens=1000,
        input_tokens=600,
        output_tokens=400,
        reasoning_tokens=0,
        cache_read_tokens=200,
        cache_write_tokens=100,
        cache_output_tokens=0,
        actual_cost=D("1.50"),
        virtual_cost=D("2.00"),
        source_cost=D("0"),
        unpriced_count=0,
        tool_call_count=5,
        tool_failure_count=0,
        user_messages=10,
        assistant_messages=10,
        models=("gpt-4o",),
        providers=("openai",),
    )
    defaults.update(kwargs)
    return InsightSessionMeta(
        **{
            k: v
            for k, v in defaults.items()
            if v is not None
            or k
            in (
                "area_path",
                "area_name",
                "origin_machine_id",
                "source_path",
                "cwd",
                "first_prompt_preview",
            )
        }
    )  # type: ignore[arg-type]


class TestRenderInsightsMarkdown:
    def test_basic_report_renders(self):
        report = InsightsReport(
            filters={"area": "private/toktrail"},
            period_label="this week",
            current=InsightAggregate(
                session_count=8,
                message_count=160,
                total_tokens=1200000,
                input_tokens=800000,
                output_tokens=400000,
                reasoning_tokens=50000,
                cache_read_tokens=200000,
                cache_write_tokens=100000,
                cache_output_tokens=0,
                actual_cost=Decimal("0.00"),
                virtual_cost=Decimal("14.72"),
                source_cost=Decimal("0"),
                unpriced_count=1,
                tool_call_count=45,
                tool_failure_count=3,
            ),
        )
        md = render_insights_markdown(report)
        assert "# toktrail insights" in md
        assert "At a glance" in md
        assert "8" in md  # session count
        assert "$14.72" in md  # virtual cost

    def test_report_with_deltas(self):
        current = InsightAggregate(
            session_count=5,
            total_tokens=1000,
            virtual_cost=Decimal("10.00"),
            input_tokens=600,
            output_tokens=400,
            cache_read_tokens=200,
            cache_write_tokens=100,
        )
        previous = InsightAggregate(
            session_count=3,
            total_tokens=500,
            virtual_cost=Decimal("5.00"),
            input_tokens=300,
            output_tokens=200,
            cache_read_tokens=100,
            cache_write_tokens=50,
        )
        delta = InsightDelta(
            metric="virtual_cost",
            current=Decimal("10.00"),
            previous=Decimal("5.00"),
            change="+100%",
            direction="up",
        )
        report = InsightsReport(
            filters={},
            period_label="this week",
            current=current,
            previous=previous,
            deltas=(delta,),
        )
        md = render_insights_markdown(report)
        assert "What changed" in md
        assert "virtual_cost" in md

    def test_report_with_anomalies(self):
        report = InsightsReport(
            filters={},
            period_label="today",
            current=InsightAggregate(session_count=1),
            anomalies=(
                InsightAnomaly(
                    kind="cost",
                    severity="high",
                    session_key="codex/sess-001",
                    message="Session cost exceeds 3x median",
                    value=Decimal("50.00"),
                    baseline=Decimal("10.00"),
                ),
            ),
        )
        md = render_insights_markdown(report)
        assert "Anomalies" in md
        assert "Session cost exceeds 3x median" in md

    def test_report_with_suggestions(self):
        report = InsightsReport(
            filters={},
            period_label="today",
            current=InsightAggregate(session_count=1),
            suggestions=(
                DeterministicSuggestion(
                    kind="unpriced_models",
                    severity="warning",
                    title="Add missing price configuration",
                    detail="3 models had usage but no actual price resolution.",
                    command="toktrail config prices",
                ),
            ),
        )
        md = render_insights_markdown(report)
        assert "Deterministic suggestions" in md
        assert "Add missing price configuration" in md
        assert "`toktrail config prices`" in md

    def test_report_with_groups(self):
        report = InsightsReport(
            filters={},
            period_label="this week",
            current=InsightAggregate(
                session_count=3,
                by_harness=(
                    InsightGroupRow(
                        key="codex",
                        label="codex",
                        session_count=2,
                        total_tokens=5000,
                        virtual_cost=Decimal("10.00"),
                    ),
                    InsightGroupRow(
                        key="pi",
                        label="pi",
                        session_count=1,
                        total_tokens=2000,
                        virtual_cost=Decimal("4.00"),
                    ),
                ),
            ),
        )
        md = render_insights_markdown(report)
        assert "Usage by harness" in md
        assert "codex" in md
        assert "pi" in md

    def test_no_html_in_output(self):
        report = InsightsReport(
            filters={},
            period_label="today",
            current=InsightAggregate(session_count=1),
        )
        md = render_insights_markdown(report)
        assert "<html" not in md
        assert "<div" not in md
        assert "<script" not in md

    def test_appendix_present(self):
        report = InsightsReport(
            filters={},
            period_label="today",
            current=InsightAggregate(session_count=1),
        )
        md = render_insights_markdown(report)
        assert "Appendix: assumptions" in md
        assert "No LLM or network calls used" in md

    def test_sessions_to_inspect_table(self):
        sessions = (
            _make_session(
                source_session_id="abc-123-def",
                virtual_cost=Decimal("5.00"),
                tool_failure_count=2,
            ),
        )
        report = InsightsReport(
            filters={},
            period_label="today",
            current=InsightAggregate(
                session_count=1,
                virtual_cost=Decimal("5.00"),
            ),
            sessions_to_inspect=sessions,
        )
        md = render_insights_markdown(report)
        assert "Sessions to inspect" in md
        assert "abc-123-def" in md

    def test_filters_displayed(self):
        report = InsightsReport(
            filters={"area": "private/toktrail", "harness": "codex"},
            period_label="this week",
            current=InsightAggregate(session_count=1),
        )
        md = render_insights_markdown(report)
        assert "Filters:" in md
        assert "area" in md
