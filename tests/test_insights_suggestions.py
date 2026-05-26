"""Tests for deterministic suggestion rules."""

from __future__ import annotations

from decimal import Decimal

from toktrail.insights.models import (
    InsightAggregate,
    InsightGroupRow,
    InsightSessionMeta,
)
from toktrail.insights.suggestions import generate_suggestions


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


def _make_aggregate(
    session_count: int = 5,
    total_tokens: int = 5000,
    input_tokens: int = 3000,
    output_tokens: int = 2000,
    cache_read_tokens: int = 500,
    cache_write_tokens: int = 200,
    cache_output_tokens: int = 0,
    virtual_cost: Decimal = Decimal("10.00"),
    actual_cost: Decimal = Decimal("8.00"),
    unpriced_count: int = 0,
    tool_failure_count: int = 0,
    by_area: tuple[InsightGroupRow, ...] = (),
) -> InsightAggregate:
    return InsightAggregate(
        session_count=session_count,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_output_tokens=cache_output_tokens,
        actual_cost=actual_cost,
        virtual_cost=virtual_cost,
        source_cost=Decimal(0),
        unpriced_count=unpriced_count,
        tool_failure_count=tool_failure_count,
        by_area=by_area,
    )


class TestGenerateSuggestions:
    def test_no_suggestions_for_clean_state(self):
        current = _make_aggregate()
        # Session with no issues: has area, machine, no failures, no unpriced, etc.
        s = _make_session(
            area_path="work/project",
            origin_machine_id="machine-1",
            tool_failure_count=0,
            unpriced_count=0,
        )
        suggestions = generate_suggestions(current, None, (s,))
        assert len(suggestions) == 0

    def test_unpriced_models_suggestion(self):
        current = _make_aggregate(unpriced_count=10)
        sessions = (
            _make_session(
                source_session_id="s1",
                unpriced_count=5,
                models=("unknown-model",),
            ),
        )
        suggestions = generate_suggestions(current, None, sessions)
        kinds = [s["kind"] for s in suggestions]
        assert "unpriced_models" in kinds

    def test_tool_failures_suggestion(self):
        sessions = (
            _make_session(
                source_session_id="s1",
                tool_failure_count=5,
            ),
        )
        current = _make_aggregate()
        suggestions = generate_suggestions(current, None, sessions)
        kinds = [s["kind"] for s in suggestions]
        assert "tool_failures" in kinds

    def test_machine_name_missing_suggestion(self):
        sessions = (_make_session(origin_machine_id=None),)
        current = _make_aggregate()
        suggestions = generate_suggestions(current, None, sessions)
        kinds = [s["kind"] for s in suggestions]
        assert "machine_name_missing" in kinds

    def test_no_area_suggestion(self):
        sessions = (_make_session(area_path=None, virtual_cost=Decimal("5.00")),)
        current = _make_aggregate()
        suggestions = generate_suggestions(current, None, sessions)
        kinds = [s["kind"] for s in suggestions]
        assert "no_area" in kinds

    def test_area_dominance_suggestion(self):
        area_row = InsightGroupRow(
            key="work/project",
            label="work/project",
            session_count=1,
            virtual_cost=Decimal("8.00"),
        )
        current = _make_aggregate(
            virtual_cost=Decimal("10.00"),
            by_area=(area_row,),
        )
        sessions = (
            _make_session(area_path="work/project", virtual_cost=Decimal("8.00")),
            _make_session(area_path="other", virtual_cost=Decimal("2.00")),
        )
        suggestions = generate_suggestions(current, None, sessions)
        kinds = [s["kind"] for s in suggestions]
        assert "area_dominance" in kinds

    def test_cost_spike_suggestion(self):
        current = _make_aggregate(virtual_cost=Decimal("20.00"))
        previous = _make_aggregate(virtual_cost=Decimal("5.00"))
        sessions = (_make_session(),)
        suggestions = generate_suggestions(current, previous, sessions)
        kinds = [s["kind"] for s in suggestions]
        assert "cost_spike" in kinds

    def test_suggestions_have_required_fields(self):
        current = _make_aggregate(unpriced_count=5)
        sessions = (_make_session(models=("model-x",), unpriced_count=5),)
        suggestions = generate_suggestions(current, None, sessions)
        assert len(suggestions) > 0
        for s in suggestions:
            assert "kind" in s
            assert "severity" in s
            assert "title" in s
            assert "detail" in s

    def test_model_repeatedly_unpriced(self):
        sessions = (
            _make_session(
                source_session_id="s1",
                models=("model-x",),
                unpriced_count=3,
            ),
            _make_session(
                source_session_id="s2",
                models=("model-x",),
                unpriced_count=2,
            ),
        )
        current = _make_aggregate(unpriced_count=5)
        suggestions = generate_suggestions(current, None, sessions)
        kinds = [s["kind"] for s in suggestions]
        assert "model_repeatedly_unpriced" in kinds
