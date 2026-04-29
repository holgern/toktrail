from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _manual_run_common import build_parser, run_manual_example


def main() -> int:
    parser = build_parser(
        harness="opencode",
        default_db=".toktrail/opencode-example.db",
    )
    args = parser.parse_args()
    return run_manual_example(
        harness="opencode",
        display_name="OpenCode",
        db_path=args.db,
        source_path=args.source,
        shell=args.shell,
        source_session_id=args.source_session_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
