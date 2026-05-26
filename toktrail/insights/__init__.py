"""Internal insights package for toktrail.

Provides deterministic extraction, aggregation, temporal comparison,
anomaly detection, suggestion rules, and Markdown rendering for
usage insights reports.  No HTML, no LLM, no network calls.
"""

from toktrail.insights.models import (
    DeterministicSuggestion,
    InsightAggregate,
    InsightAnomaly,
    InsightDelta,
    InsightGroupRow,
    InsightSessionMeta,
    InsightsReport,
)

__all__ = [
    "DeterministicSuggestion",
    "InsightAggregate",
    "InsightAnomaly",
    "InsightDelta",
    "InsightGroupRow",
    "InsightSessionMeta",
    "InsightsReport",
]
