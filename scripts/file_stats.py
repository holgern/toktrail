#!/usr/bin/env python3
"""Print architecture-oriented Python file and function size statistics."""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FunctionStat:
    path: Path
    name: str
    lines: int
    start_line: int
    end_line: int


@dataclass(frozen=True)
class FileStat:
    path: Path
    lines: int
    functions: int
    classes: int
    max_function_lines: int
    long_functions: tuple[FunctionStat, ...]


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and ".taskledger" not in path.parts
    )


def _iter_top_level_functions(
    tree: ast.AST,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _node_end_lineno(node: ast.AST) -> int:
    return int(getattr(node, "end_lineno", getattr(node, "lineno", 0)))


def _analyze(path: Path) -> FileStat:
    source = path.read_text(encoding="utf-8")
    lines = source.count("\n") + (0 if not source else 1)
    tree = ast.parse(source)
    class_count = sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree))
    functions = _iter_top_level_functions(tree)

    function_stats: list[FunctionStat] = []
    for node in functions:
        start_line = int(getattr(node, "lineno", 0))
        end_line = _node_end_lineno(node)
        length = max(end_line - start_line + 1, 0)
        function_stats.append(
            FunctionStat(
                path=path,
                name=node.name,
                lines=length,
                start_line=start_line,
                end_line=end_line,
            )
        )

    return FileStat(
        path=path,
        lines=lines,
        functions=len(functions),
        classes=class_count,
        max_function_lines=max((item.lines for item in function_stats), default=0),
        long_functions=tuple(item for item in function_stats if item.lines > 80),
    )


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fail-over-lines",
        type=int,
        default=None,
        help="Exit non-zero when any file line count exceeds this threshold.",
    )
    parser.add_argument(
        "--top-files",
        type=int,
        default=20,
        help="Number of largest files to print (default: 20).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    root = Path(__file__).resolve().parents[1]

    file_stats = [_analyze(path) for path in _iter_python_files(root)]
    file_stats.sort(key=lambda item: item.lines, reverse=True)

    print("## Top files by lines")
    print("| Path | Lines | Functions | Classes | Max function lines |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for stat in file_stats[: args.top_files]:
        print(
            f"| {_relative(stat.path, root)} | {stat.lines} | {stat.functions} | "
            f"{stat.classes} | {stat.max_function_lines} |"
        )

    oversized_functions = sorted(
        (fn for stat in file_stats for fn in stat.long_functions),
        key=lambda item: item.lines,
        reverse=True,
    )
    print("\n## Functions over 80 lines")
    print("| Path | Function | Range | Lines |")
    print("| --- | --- | ---: | ---: |")
    for fn in oversized_functions:
        rel = _relative(fn.path, root)
        print(f"| {rel} | {fn.name} | L{fn.start_line}-L{fn.end_line} | {fn.lines} |")

    large_modules = [stat for stat in file_stats if stat.lines > 800]
    print("\n## Modules over 800 lines")
    if not large_modules:
        print("(none)")
    else:
        print("| Path | Lines |")
        print("| --- | ---: |")
        for stat in large_modules:
            print(f"| {_relative(stat.path, root)} | {stat.lines} |")

    if args.fail_over_lines is not None:
        offenders = [stat for stat in file_stats if stat.lines > args.fail_over_lines]
        if offenders:
            print(
                f"\nFAIL: {len(offenders)} modules exceed {args.fail_over_lines} lines:"
            )
            for stat in offenders:
                print(
                    f"- {_relative(stat.path, root)} ({stat.lines} lines)",
                    file=sys.stderr,
                )
            raise SystemExit(1)


if __name__ == "__main__":
    main()
