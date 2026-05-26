"""Tests for temporal comparison and anomaly detection."""

from __future__ import annotations

from decimal import Decimal

from toktrail.insights.models import (
    InsightAggregate,
    InsightGroupRow,
    InsightSessionMeta,
)
from toktrail.insights.temporal import (
    compute_deltas,
    detect_anomalies,
    resolve_previous_period,
)


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
    # Filter out None values that should remain None
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


def _make_aggregate(
    session_count: int = 5,
    total_tokens: int = 5000,
    virtual_cost: Decimal = Decimal("10.00"),
    actual_cost: Decimal = Decimal("8.00"),
    input_tokens: int = 3000,
    output_tokens: int = 2000,
    unpriced_count: int = 0,
    tool_failure_count: int = 0,
    cache_read_tokens: int = 500,
    cache_write_tokens: int = 200,
    by_area: tuple[InsightGroupRow, ...] = (),
) -> InsightAggregate:
    return InsightAggregate(
        session_count=session_count,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        actual_cost=actual_cost,
        virtual_cost=virtual_cost,
        source_cost=Decimal(0),
        unpriced_count=unpriced_count,
        tool_failure_count=tool_failure_count,
        by_area=by_area,
    )


class TestResolvePreviousPeriod:
    def test_basic_previous_period(self):
        prev = resolve_previous_period(1000, 2000)
        assert prev == (0, 1000)

    def test_none_since(self):
        prev = resolve_previous_period(None, 2000)
        assert prev == (None, None)

    def test_none_until(self):
        prev = resolve_previous_period(1000, None)
        assert prev == (None, None)

    def test_inverted_period(self):
        prev = resolve_previous_period(2000, 1000)
        assert prev == (None, None)


class TestComputeDeltas:
    def test_no_previous(self):
        current = _make_aggregate()
        deltas = compute_deltas(current, None)
        assert deltas == ()

    def test_basic_deltas(self):
        current = _make_aggregate(virtual_cost=Decimal("10.00"))
        previous = _make_aggregate(virtual_cost=Decimal("5.00"))
        deltas = compute_deltas(current, previous)
        assert len(deltas) > 0
        # Find the virtual_cost delta
        vc_delta = [d for d in deltas if d.metric == "virtual_cost"]
        assert len(vc_delta) == 1
        assert vc_delta[0].direction == "up"
        assert vc_delta[0].change == "+100%"

    def test_cost_decrease(self):
        current = _make_aggregate(virtual_cost=Decimal("3.00"))
        previous = _make_aggregate(virtual_cost=Decimal("6.00"))
        deltas = compute_deltas(current, previous)
        vc_delta = [d for d in deltas if d.metric == "virtual_cost"]
        assert vc_delta[0].direction == "down"

    def test_new_metric(self):
        current = _make_aggregate(virtual_cost=Decimal("5.00"))
        previous = _make_aggregate(virtual_cost=Decimal("0"))
        deltas = compute_deltas(current, previous)
        vc_delta = [d for d in deltas if d.metric == "virtual_cost"]
        assert vc_delta[0].direction == "new"

    def test_flat_change(self):
        current = _make_aggregate(
            virtual_cost=Decimal("10.00"),
            total_tokens=1000,
        )
        previous = _make_aggregate(
            virtual_cost=Decimal("10.00"),
            total_tokens=1000,
        )
        deltas = compute_deltas(current, previous)
        flat_deltas = [d for d in deltas if d.direction == "flat"]
        assert len(flat_deltas) > 0


class TestDetectAnomalies:
    def test_no_anomalies_with_no_sessions(self):
        current = _make_aggregate()
        anomalies = detect_anomalies(current, ())
        assert anomalies == ()

    def test_cost_outlier(self):
        sessions = (
            _make_session(
                source_session_id="cheap",
                virtual_cost=Decimal("1.00"),
            ),
            _make_session(
                source_session_id="expensive",
                virtual_cost=Decimal("50.00"),
            ),
            _make_session(
                source_session_id="mid",
                virtual_cost=Decimal("3.00"),
            ),
        )
        current = _make_aggregate()
        anomalies = detect_anomalies(current, sessions)
        cost_anomalies = [a for a in anomalies if a.kind == "cost"]
        assert len(cost_anomalies) >= 1
        assert cost_anomalies[0].severity == "high"

    def test_failure_outlier(self):
        sessions = (
            _make_session(
                source_session_id="good",
                tool_call_count=10,
                tool_failure_count=0,
            ),
            _make_session(
                source_session_id="bad",
                tool_call_count=10,
                tool_failure_count=5,
            ),
            _make_session(
                source_session_id="ok",
                tool_call_count=10,
                tool_failure_count=1,
            ),
        )
        current = _make_aggregate()
        anomalies = detect_anomalies(current, sessions)
        error_anomalies = [a for a in anomalies if a.kind == "errors"]
        assert len(error_anomalies) >= 1

    def test_machine_name_missing(self):
        sessions = (_make_session(origin_machine_id=None),)
        current = _make_aggregate()
        anomalies = detect_anomalies(current, sessions)
        machine_anomalies = [a for a in anomalies if a.kind == "cost"]
        assert any("machine" in a.message.lower() for a in machine_anomalies)

    def test_area_dominance(self):
        sessions = (
            _make_session(area_path="work/project", virtual_cost=Decimal("9.00")),
            _make_session(area_path="private/toktrail", virtual_cost=Decimal("1.00")),
        )
        area_row = InsightGroupRow(
            key="work/project",
            label="work/project",
            session_count=1,
            virtual_cost=Decimal("9.00"),
        )
        current = _make_aggregate(
            virtual_cost=Decimal("10.00"),
            by_area=(area_row,),
        )
        anomalies = detect_anomalies(current, sessions)
        assert any(a.kind == "cost" and "Area" in a.message for a in anomalies)
