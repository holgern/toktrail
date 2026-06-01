"""Tests for stats report computation: distributions, archetypes, health, area mix."""

from __future__ import annotations

from decimal import Decimal

from toktrail.api.reports import (
    _compute_archetypes,
    _compute_area_mix,
    _compute_distributions,
    _compute_health_aggregates,
)
from toktrail.insights.models import InsightSessionMeta


def _meta(
    *,
    harness: str = "pi",
    source_session_id: str = "s1",
    origin_machine_id: str | None = "m1",
    duration_ms: int | None = 60_000,
    user_messages: int = 1,
    assistant_messages: int = 1,
    total_tokens: int = 100,
    actual_cost: Decimal = Decimal("0.01"),
    area_path: str | None = None,
) -> InsightSessionMeta:
    return InsightSessionMeta(
        harness=harness,
        source_session_id=source_session_id,
        origin_machine_id=origin_machine_id,
        duration_ms=duration_ms,
        user_messages=user_messages,
        assistant_messages=assistant_messages,
        total_tokens=total_tokens,
        actual_cost=actual_cost,
        area_path=area_path,
    )


class TestComputeDistributions:
    def test_empty(self) -> None:
        result = _compute_distributions(())
        assert result["user_messages"]["median"] == 0
        assert result["total_messages"]["median"] == 0
        assert "duration_ms" not in result

    def test_duration_histogram(self) -> None:
        metas = (
            _meta(duration_ms=30_000),  # 30s => "<1m"
            _meta(duration_ms=120_000),  # 2m => "1-5m"
            _meta(duration_ms=1_800_000),  # 30m => "30-60m"
        )
        result = _compute_distributions(metas)
        dur = result["duration_ms"]
        assert isinstance(dur, dict)
        hist = dur["histogram"]
        assert hist.get("<1m") == 1
        assert hist.get("1-5m") == 1
        assert hist.get("30-60m") == 1

    def test_message_histogram(self) -> None:
        metas = (
            _meta(user_messages=0, assistant_messages=0),
            _meta(user_messages=5, assistant_messages=5),
            _meta(user_messages=50, assistant_messages=50),
        )
        result = _compute_distributions(metas)
        assert result["user_messages"]["histogram"].get("0") == 1
        assert result["user_messages"]["histogram"].get("4-10") == 1
        assert result["user_messages"]["histogram"].get("31-100") == 1

    def test_median(self) -> None:
        metas = tuple(_meta(duration_ms=d * 60_000) for d in [1, 5, 10, 20, 30])
        result = _compute_distributions(metas)
        dur = result["duration_ms"]
        # sorted durations: [60000, 300000, 600000, 1200000, 1800000]
        # median at index 2 = 600000
        assert dur["median"] == 600_000


class TestComputeArchetypes:
    def test_empty(self) -> None:
        result = _compute_archetypes(())
        assert result["counts"]["automation"] == 0
        assert result["fractions"]["automation"] == 0.0

    def test_classification(self) -> None:
        metas = (
            # automation: <1m, <=2 msgs
            _meta(
                duration_ms=30_000,
                user_messages=1,
                assistant_messages=0,
            ),
            # quick: 2m, 4 msgs
            _meta(
                duration_ms=120_000,
                user_messages=2,
                assistant_messages=2,
            ),
            # standard: 10m, 20 msgs
            _meta(
                duration_ms=600_000,
                user_messages=10,
                assistant_messages=10,
            ),
            # deep: 60m, 60 msgs
            _meta(
                duration_ms=3_600_000,
                user_messages=30,
                assistant_messages=30,
            ),
            # marathon: 120m+
            _meta(
                duration_ms=7_200_000,
                user_messages=100,
                assistant_messages=100,
            ),
        )
        result = _compute_archetypes(metas)
        assert result["counts"]["automation"] == 1
        assert result["counts"]["quick"] == 1
        assert result["counts"]["standard"] == 1
        assert result["counts"]["deep"] == 1
        assert result["counts"]["marathon"] == 1
        assert result["fractions"]["automation"] == 0.2

    def test_zero_duration_defaults_to_automation_if_few_msgs(self) -> None:
        metas = (_meta(duration_ms=0, user_messages=1, assistant_messages=0),)
        result = _compute_archetypes(metas)
        assert result["counts"]["automation"] == 1


class TestComputeHealthAggregates:
    def test_empty(self) -> None:
        result = _compute_health_aggregates(None, ())
        assert result == {}

    def test_no_digests(self, tmp_path) -> None:
        from toktrail.db import connect, migrate

        db_path = tmp_path / "test.db"
        conn = connect(db_path)
        migrate(conn)
        conn.close()
        metas = (_meta(),)
        result = _compute_health_aggregates(db_path, metas)
        # No digests in fresh db, so sessions_with_health should be 0
        assert result.get("sessions_with_health", 0) == 0


class TestComputeAreaMix:
    def test_empty(self) -> None:
        result = _compute_area_mix(())
        assert result == ()

    def test_grouping(self) -> None:
        metas = (
            _meta(area_path="area/project-a"),
            _meta(area_path="area/project-b"),
            _meta(area_path="area/project-a"),
        )
        result = _compute_area_mix(metas)
        assert len(result) == 2
        a_row = [r for r in result if r["area"] == "area/project-a"][0]
        assert a_row["sessions"] == 2
        b_row = [r for r in result if r["area"] == "area/project-b"][0]
        assert b_row["sessions"] == 1

    def test_unassigned(self) -> None:
        metas = (_meta(area_path=None),)
        result = _compute_area_mix(metas)
        assert len(result) == 1
        assert result[0]["area"] == "(unassigned)"
