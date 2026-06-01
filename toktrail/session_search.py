"""Opt-in redacted session search index.

Stores centrally redacted snippets/fields from session transcripts
in a rebuildable SQLite index. Never stores raw transcript content
or secrets by default. FTS5 is used when available, with a fallback
substring search.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

# Central redaction patterns for text content.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"""(api[_-]?key|token|secret|password|auth)[\s:=]+['\"]?\S+""",
        re.IGNORECASE,
    ),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),
    re.compile(r"gho_[a-zA-Z0-9]{36}"),
    re.compile(r"github_pat_[a-zA-Z0-9_]{82}"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
)

_REDACTED = "[REDACTED]"


def redact_text(text: str, max_chars: int = 500) -> str:
    """Redact secrets and truncate text to max_chars."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return text


@dataclass(frozen=True)
class SessionIndexItem:
    origin_machine_id: str
    harness: str
    source_session_id: str
    ordinal: int
    kind: str
    created_ms: int | None
    content_redacted: str
    source_fingerprint: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "origin_machine_id": self.origin_machine_id,
            "harness": self.harness,
            "source_session_id": self.source_session_id,
            "ordinal": self.ordinal,
            "kind": self.kind,
            "created_ms": self.created_ms,
            "content_redacted": self.content_redacted,
            "source_fingerprint": self.source_fingerprint,
        }


def _create_index_tables(conn: sqlite3.Connection) -> None:
    """Create session index tables if they don't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_index_items (
            origin_machine_id TEXT NOT NULL,
            harness TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL,
            created_ms INTEGER,
            content_redacted TEXT NOT NULL,
            source_fingerprint TEXT,
            PRIMARY KEY (origin_machine_id, harness, source_session_id, ordinal, kind)
        )
        """
    )
    # Try FTS5 virtual table
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS session_index_fts USING fts5(
                origin_machine_id UNINDEXED,
                harness UNINDEXED,
                source_session_id UNINDEXED,
                kind UNINDEXED,
                ordinal UNINDEXED,
                content_redacted,
                tokenize='unicode61'
            )
            """
        )
    except sqlite3.OperationalError:
        pass  # FTS5 not available


def _has_fts5(conn: sqlite3.Connection) -> bool:
    """Check if FTS5 virtual table exists."""
    try:
        conn.execute("SELECT count(*) FROM session_index_fts LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def index_session_items(
    conn: sqlite3.Connection,
    items: tuple[SessionIndexItem, ...],
) -> int:
    """Insert redacted index items. Returns count of inserted rows."""
    if not items:
        return 0
    _create_index_tables(conn)
    has_fts = _has_fts5(conn)
    count = 0
    for item in items:
        conn.execute(
            """
            INSERT OR REPLACE INTO session_index_items
                (origin_machine_id, harness, source_session_id,
                 ordinal, kind, created_ms, content_redacted,
                 source_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.origin_machine_id,
                item.harness,
                item.source_session_id,
                item.ordinal,
                item.kind,
                item.created_ms,
                item.content_redacted,
                item.source_fingerprint,
            ),
        )
        if has_fts:
            conn.execute(
                """
                DELETE FROM session_index_fts
                WHERE origin_machine_id = ?
                  AND harness = ?
                  AND source_session_id = ?
                  AND ordinal = ?
                  AND kind = ?
                """,
                (
                    item.origin_machine_id,
                    item.harness,
                    item.source_session_id,
                    item.ordinal,
                    item.kind,
                ),
            )
            conn.execute(
                """
                INSERT INTO session_index_fts
                    (origin_machine_id, harness, source_session_id,
                     kind, ordinal, content_redacted)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.origin_machine_id,
                    item.harness,
                    item.source_session_id,
                    item.kind,
                    item.ordinal,
                    item.content_redacted,
                ),
            )
        count += 1
    conn.commit()
    return count


def search_session_index(
    conn: sqlite3.Connection,
    query: str,
    *,
    harness: str | None = None,
    limit: int = 50,
) -> tuple[SessionIndexItem, ...]:
    """Search the session index. Uses FTS5 if available, else substring."""
    _create_index_tables(conn)
    like_pattern = f"%{query}%"
    like_params: list[object] = [like_pattern, limit]
    fts_params: list[object] = [query, limit]
    if harness is not None:
        like_params = [like_pattern, harness, limit]
        fts_params = [query, harness, limit]

    if _has_fts5(conn):
        if harness is not None:
            rows = conn.execute(
                """
                SELECT si.origin_machine_id, si.harness,
                       si.source_session_id, si.ordinal,
                       si.kind, si.created_ms,
                       si.content_redacted, si.source_fingerprint
                FROM session_index_fts fts
                JOIN session_index_items si
                    ON fts.origin_machine_id = si.origin_machine_id
                    AND fts.harness = si.harness
                    AND fts.source_session_id = si.source_session_id
                    AND fts.ordinal = si.ordinal
                    AND fts.kind = si.kind
                WHERE session_index_fts MATCH ? AND fts.harness = ?
                ORDER BY si.created_ms DESC
                LIMIT ?
                """,
                tuple(fts_params),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT si.origin_machine_id, si.harness,
                       si.source_session_id, si.ordinal,
                       si.kind, si.created_ms,
                       si.content_redacted, si.source_fingerprint
                FROM session_index_fts fts
                JOIN session_index_items si
                    ON fts.origin_machine_id = si.origin_machine_id
                    AND fts.harness = si.harness
                    AND fts.source_session_id = si.source_session_id
                    AND fts.ordinal = si.ordinal
                    AND fts.kind = si.kind
                WHERE session_index_fts MATCH ?
                ORDER BY si.created_ms DESC
                LIMIT ?
                """,
                tuple(fts_params),
            ).fetchall()
    else:
        if harness is not None:
            rows = conn.execute(
                """
                SELECT origin_machine_id, harness, source_session_id,
                       ordinal, kind, created_ms, content_redacted,
                       source_fingerprint
                FROM session_index_items
                WHERE content_redacted LIKE ? AND harness = ?
                ORDER BY created_ms DESC
                LIMIT ?
                """,
                tuple(like_params),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT origin_machine_id, harness, source_session_id,
                       ordinal, kind, created_ms, content_redacted,
                       source_fingerprint
                FROM session_index_items
                WHERE content_redacted LIKE ?
                ORDER BY created_ms DESC
                LIMIT ?
                """,
                tuple(like_params),
            ).fetchall()

    return tuple(
        SessionIndexItem(
            origin_machine_id=row[0],
            harness=row[1],
            source_session_id=row[2],
            ordinal=row[3],
            kind=row[4],
            created_ms=row[5],
            content_redacted=row[6],
            source_fingerprint=row[7],
        )
        for row in rows
    )


def clear_session_index(conn: sqlite3.Connection) -> int:
    """Clear all index rows. Returns count deleted."""
    _create_index_tables(conn)
    count_row = conn.execute("SELECT count(*) FROM session_index_items").fetchone()[0]
    count: int = int(count_row) if count_row is not None else 0
    conn.execute("DELETE FROM session_index_items")
    if _has_fts5(conn):
        conn.execute("DELETE FROM session_index_fts")
    conn.commit()
    return count
