"""Tests for the insights API wrapper."""

from __future__ import annotations

from toktrail.api.insights import insights_report as insights_api


class TestInsightsReportAPI:
    """Test that the API function exists and has the right signature."""

    def test_api_function_importable(self):
        """The API function should be importable from toktrail.api.insights."""
        assert callable(insights_api)

    def test_api_function_signature(self):
        """The API function should accept the expected keyword arguments."""
        import inspect

        sig = inspect.signature(insights_api)
        assert "db_path" in sig.parameters
        assert "period" in sig.parameters
        assert "area" in sig.parameters
        assert "harnesses" in sig.parameters
        assert "json_output" not in sig.parameters  # CLI concern, not API

    def test_api_function_return_type(self):
        """The return type annotation should reference InsightsReport."""
        import inspect

        sig = inspect.signature(insights_api)
        # With from __future__ import annotations, annotation is a string
        assert "InsightsReport" in str(sig.return_annotation)
