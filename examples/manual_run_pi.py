from __future__ import annotations

from examples._manual_run_common import build_parser, run_manual_example


def main() -> int:
    parser = build_parser(harness="pi", default_db=".toktrail/pi-example.db")
    args = parser.parse_args()
    return run_manual_example(
        harness="pi",
        display_name="Pi",
        db_path=args.db,
        source_path=args.source,
        shell=args.shell,
        source_session_id=args.source_session_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
