#!/usr/bin/env python3
"""Print top Python files by line count and simple symbol counts."""

from __future__ import annotations

import ast
from pathlib import Path


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and ".taskledger" not in path.parts
    )


def _analyze(path: Path) -> tuple[int, int, int]:
    source = path.read_text(encoding="utf-8")
    lines = source.count("\n") + (0 if not source else 1)
    tree = ast.parse(source)
    functions = sum(isinstance(node, ast.FunctionDef) for node in ast.walk(tree))
    classes = sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree))
    return lines, functions, classes


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    rows = []
    for path in _iter_python_files(root):
        lines, functions, classes = _analyze(path)
        rows.append((lines, functions, classes, path.relative_to(root)))
    rows.sort(reverse=True)

    print("| Path | Lines | Functions | Classes |")
    print("| --- | ---: | ---: | ---: |")
    for lines, functions, classes, rel in rows[:20]:
        print(f"| {rel.as_posix()} | {lines} | {functions} | {classes} |")


if __name__ == "__main__":
    main()
