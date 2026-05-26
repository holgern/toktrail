"""CLI tests for toktrail insights commands."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from toktrail.cli_parts.insights import (
    InsightsRuntime,
    configure_insights_runtime,
    insights_app,
    insights_report,
)
from toktrail.insights.models import (
    InsightAggregate,
    InsightsReport,
)


def _make_report():
    return InsightsReport(
        filters={"area": "test"},
        period_label="today",
        current=InsightAggregate(
            session_count=3,
            message_count=30,
            total_tokens=5000,
            input_tokens=3000,
            output_tokens=2000,
            reasoning_tokens=100,
            cache_read_tokens=500,
            cache_write_tokens=200,
            cache_output_tokens=0,
            actual_cost=Decimal("2.50"),
            virtual_cost=Decimal("5.00"),
            source_cost=Decimal("0"),
            unpriced_count=0,
            tool_call_count=10,
            tool_failure_count=1,
        ),
    )


class TestInsightsCLIRegistration:
    """Test that the insights command is properly registered."""

    def test_insights_app_exists(self):
        assert insights_app is not None

    def test_insights_help_works(self):
        runner = CliRunner()
        result = runner.invoke(insights_app, ["report", "--help"])
        assert result.exit_code == 0
        assert "period" in result.output.lower() or "PERIOD" in result.output

    def test_insights_command_function_exists(self):
        assert callable(insights_report)


class TestInsightsRuntime:
    """Test that the insights runtime configuration works."""

    def test_runtime_configuration(self):
        # Configure runtime with mock functions
        mock_db_path = Path("/tmp/test.db")
        mock_config_path = Path("/tmp/test.toml")

        runtime = InsightsRuntime(
            resolve_state_db=lambda ctx: mock_db_path,
            resolve_config_path=lambda ctx: mock_config_path,
            exit_with_error=lambda msg: None,
        )
        configure_insights_runtime(runtime)
        # After configuration, calling the runtime should work
        assert runtime.resolve_state_db(None) == mock_db_path
        assert runtime.resolve_config_path(None) == mock_config_path
