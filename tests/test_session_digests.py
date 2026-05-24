from __future__ import annotations

from decimal import Decimal

from toktrail.models import TokenBreakdown
from toktrail.reporting import CostTotals, SessionTranscriptEvent, UsageSessionRow
from toktrail.session_digests import build_session_digest


def _usage_session() -> UsageSessionRow:
    return UsageSessionRow(
        key="machine/codex/ses-1",
        origin_machine_id="machine-1",
        machine_name="workstation",
        machine_label="workstation",
        harness="codex",
        source_session_id="ses-1",
        area_id=None,
        area_sync_id=None,
        area_path="private/toktrail",
        area_name=None,
        first_ms=1_000,
        last_ms=2_000,
        message_count=1,
        tokens=TokenBreakdown(input=10, output=5, reasoning=3),
        costs=CostTotals(actual_cost_usd=Decimal("0.01")),
        cwd="/home/test/src/toktrail",
        source_dir="/home/test/src/toktrail",
        git_root="/home/test/src/toktrail",
        git_remote="git@example.com:org/toktrail.git",
        session_title="Fix API token=secret-value for user@example.com",
    )


def test_build_session_digest_redacts_and_aggregates_tool_failures() -> None:
    digest = build_session_digest(
        usage_session=_usage_session(),
        transcript_events=(
            SessionTranscriptEvent(
                harness="codex",
                source_session_id="ses-1",
                created_ms=1_500,
                role=None,
                kind="command",
                name="shell",
                text="pytest tests/test_session_digests.py",
                path="tests/test_session_digests.py",
                success=False,
                error_text="failed",
                raw_kind="exec_command",
            ),
        ),
    )

    payload = digest.as_dict()
    assert payload["type"] == "session_digest"
    assert digest.tool_health.tool_call_count == 1
    assert digest.tool_health.tool_failure_count == 1
    assert digest.tool_health.failed_tools == {"shell": 1}
    assert digest.files_mentioned == ("tests/test_session_digests.py",)
    assert digest.commands_mentioned == ("pytest tests/test_session_digests.py",)
    assert "secret-value" not in str(payload)
    assert "user@example.com" not in str(payload)
    assert "artifacts" not in payload
    assert payload["privacy"]["contains_raw_transcript"] is False
    assert payload["privacy"]["contains_snippets"] is False
    assert payload["privacy"]["artifacts_included"] is False

    detailed = digest.as_dict(include_artifacts=True)
    assert detailed["artifacts"]["commands_mentioned"] == [
        "pytest tests/test_session_digests.py"
    ]
