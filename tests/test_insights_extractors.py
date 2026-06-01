"""Tests for insights extractors and aggregation."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from toktrail.db import (
    connect,
    get_local_machine_id,
    insert_usage_events,
    migrate,
    upsert_source_session_digest,
)
from toktrail.insights.extractors import (
    InsightSessionMeta,
    aggregate_sessions,
    extract_session_metas,
)
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.reporting import (
    CostTotals,
    SessionDigest,
    SessionDigestSummary,
    SessionToolHealth,
    SessionTotals,
)


def _make_session(
    harness: str = "codex",
    source_session_id: str = "sess-001",
    origin_machine_id: str | None = "machine-1",
    area_path: str | None = None,
    start_ms: int = 1700000000000,
    end_ms: int = 1700003600000,
    total_tokens: int = 1000,
    input_tokens: int = 600,
    output_tokens: int = 400,
    reasoning_tokens: int = 0,
    cache_read_tokens: int = 200,
    cache_write_tokens: int = 100,
    cache_output_tokens: int = 0,
    actual_cost: Decimal = Decimal("1.50"),
    virtual_cost: Decimal = Decimal("2.00"),
    source_cost: Decimal = Decimal("0"),
    unpriced_count: int = 0,
    tool_call_count: int = 5,
    tool_failure_count: int = 0,
    models: tuple[str, ...] = ("gpt-4o",),
    providers: tuple[str, ...] = ("openai",),
    user_messages: int = 10,
    assistant_messages: int = 10,
) -> InsightSessionMeta:
    return InsightSessionMeta(
        harness=harness,
        source_session_id=source_session_id,
        origin_machine_id=origin_machine_id,
        area_path=area_path,
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=end_ms - start_ms,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_output_tokens=cache_output_tokens,
        actual_cost=actual_cost,
        virtual_cost=virtual_cost,
        source_cost=source_cost,
        unpriced_count=unpriced_count,
        tool_call_count=tool_call_count,
        tool_failure_count=tool_failure_count,
        user_messages=user_messages,
        assistant_messages=assistant_messages,
        models=models,
        providers=providers,
    )


def _make_usage_event(*, source_session_id: str, dedup_suffix: str) -> UsageEvent:
    return UsageEvent(
        harness="codex",
        source_session_id=source_session_id,
        source_row_id=f"row-{dedup_suffix}",
        source_message_id=f"msg-{dedup_suffix}",
        source_dedup_key=f"msg-{dedup_suffix}",
        global_dedup_key=f"codex:msg-{dedup_suffix}",
        fingerprint_hash=f"fingerprint-{dedup_suffix}",
        provider_id="openai",
        model_id="gpt-5",
        thinking_level=None,
        agent="assistant",
        created_ms=1_700_000_000_000,
        completed_ms=1_700_000_000_500,
        tokens=TokenBreakdown(input=6, output=4),
        source_cost_usd=Decimal("0"),
        raw_json="{}",
    )


def _make_digest(*, machine_id: str, source_session_id: str) -> SessionDigest:
    return SessionDigest(
        schema_version=1,
        origin_machine_id=machine_id,
        machine_label=None,
        harness="codex",
        source_session_id=source_session_id,
        area_path=None,
        cwd=None,
        source_dir=None,
        git_root=None,
        git_remote=None,
        session_title=None,
        started_ms=None,
        last_seen_ms=None,
        usage=SessionTotals(tokens=TokenBreakdown(), costs=CostTotals()),
        message_count=1,
        summary=SessionDigestSummary(
            one_line="codex session",
            bullets=("Tool calls: 3, failures: 2",),
            confidence="high",
        ),
        tool_health=SessionToolHealth(
            tool_call_count=3,
            tool_failure_count=2,
            tool_timeout_count=1,
            failed_tools={"shell": 2, "edit": 1},
        ),
        generated_at_ms=1_700_000_001_000,
        source_fingerprint="digest-1",
    )


def _seed_usage_session(db_path: Path, *, source_session_id: str) -> str:
    conn = connect(db_path)
    try:
        migrate(conn)
        machine_id = get_local_machine_id(conn)
        insert_usage_events(
            conn,
            None,
            [_make_usage_event(source_session_id=source_session_id, dedup_suffix="1")],
        )
        conn.commit()
        return machine_id
    finally:
        conn.close()


class TestInsightSessionMeta:
    def test_cache_read_ratio(self):
        s = _make_session(
            input_tokens=800, cache_read_tokens=200, cache_write_tokens=100
        )
        assert abs(s.cache_read_ratio - 200 / 1100) < 0.001

    def test_cache_read_ratio_no_input(self):
        s = _make_session(input_tokens=0, cache_read_tokens=0)
        assert s.cache_read_ratio == 0.0

    def test_as_dict(self):
        s = _make_session()
        d = s.as_dict()
        assert d["harness"] == "codex"
        assert d["source_session_id"] == "sess-001"
        assert d["total_tokens"] == 1000
        assert "first_prompt_preview" not in d

    def test_as_dict_with_preview(self):
        s = _make_session()
        s_with_preview = InsightSessionMeta(
            **{**s.as_dict(), "first_prompt_preview": "hello world"},
        )
        assert s_with_preview.first_prompt_preview == "hello world"


class TestAggregateSessions:
    def test_empty_sessions(self):
        result = aggregate_sessions(())
        assert result.session_count == 0
        assert result.total_tokens == 0
        assert result.virtual_cost == Decimal(0)

    def test_single_session(self):
        sessions = (_make_session(),)
        result = aggregate_sessions(sessions)
        assert result.session_count == 1
        assert result.total_tokens == 1000
        assert result.virtual_cost == Decimal("2.00")
        assert result.actual_cost == Decimal("1.50")
        assert result.cache_read_ratio > 0

    def test_multiple_sessions(self):
        sessions = (
            _make_session(source_session_id="s1", virtual_cost=Decimal("5.00")),
            _make_session(source_session_id="s2", virtual_cost=Decimal("3.00")),
        )
        result = aggregate_sessions(sessions)
        assert result.session_count == 2
        assert result.virtual_cost == Decimal("8.00")
        assert result.total_tokens == 2000

    def test_grouping_by_harness(self):
        sessions = (
            _make_session(harness="codex", source_session_id="s1"),
            _make_session(harness="codex", source_session_id="s2"),
            _make_session(harness="pi", source_session_id="s3"),
        )
        result = aggregate_sessions(sessions)
        harness_keys = [row.key for row in result.by_harness]
        assert "codex" in harness_keys
        assert "pi" in harness_keys

    def test_grouping_by_model_across_models(self):
        sessions = (
            _make_session(models=("gpt-4o",)),
            _make_session(models=("gpt-4o", "o3-mini")),
        )
        result = aggregate_sessions(sessions)
        model_keys = [row.key for row in result.by_model]
        assert "gpt-4o" in model_keys
        assert "o3-mini" in model_keys

    def test_top_sessions_by_cost(self):
        sessions = (
            _make_session(source_session_id="cheap", virtual_cost=Decimal("1.00")),
            _make_session(source_session_id="expensive", virtual_cost=Decimal("10.00")),
        )
        result = aggregate_sessions(sessions)
        assert result.top_sessions[0].source_session_id == "expensive"

    def test_unpriced_count_totals(self):
        sessions = (
            _make_session(source_session_id="s1", unpriced_count=5),
            _make_session(source_session_id="s2", unpriced_count=3),
        )
        result = aggregate_sessions(sessions)
        assert result.unpriced_count == 8

    def test_tool_failure_aggregation(self):
        sessions = (
            _make_session(source_session_id="s1", tool_failure_count=2),
            _make_session(source_session_id="s2", tool_failure_count=3),
        )
        result = aggregate_sessions(sessions)
        assert result.tool_failure_count == 5

    def test_as_dict_round_trip(self):
        sessions = (_make_session(),)
        result = aggregate_sessions(sessions)
        d = result.as_dict()
        assert d["session_count"] == 1
        assert d["total_tokens"] == 1000
        assert "by_area" in d
        assert "by_model" in d


def test_extract_session_metas_uses_persisted_digest_tool_health(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "toktrail.db"
    machine_id = _seed_usage_session(db_path, source_session_id="sess-digest")

    conn = connect(db_path)
    try:
        upsert_source_session_digest(
            conn,
            _make_digest(machine_id=machine_id, source_session_id="sess-digest"),
        )
        conn.commit()
    finally:
        conn.close()

    metas = extract_session_metas(db_path=db_path)

    assert len(metas) == 1
    assert metas[0].source_session_id == "sess-digest"
    assert metas[0].tool_call_count == 3
    assert metas[0].tool_failure_count == 2
    assert metas[0].tool_failure_categories == (("shell", 2), ("edit", 1))


def test_extract_session_metas_defaults_tool_health_when_digest_missing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "toktrail.db"
    _seed_usage_session(db_path, source_session_id="sess-no-digest")

    metas = extract_session_metas(db_path=db_path)

    assert len(metas) == 1
    assert metas[0].source_session_id == "sess-no-digest"
    assert metas[0].tool_call_count == 0
    assert metas[0].tool_failure_count == 0
    assert metas[0].tool_failure_categories == ()
