#!/usr/bin/env python3
"""注释审计：列出"很长但函数体内一条注释也没有"的函数。

用途：给 PR 自查与重构提供线索，**不是**覆盖率门禁。命中后先判断该函数是否
真的需要解释——纯字符串渲染、纯字段映射可以放过；分支多、外部协议交互、
并发/顺序敏感的必须补。规范见 ``docs/commenting-guide.md``。

用法::

    python scripts/comment_audit.py                    # 默认 ≥30 行
    python scripts/comment_audit.py --min-lines 20
    python scripts/comment_audit.py --top 40 --include-tests
    python scripts/comment_audit.py --fail-over 100    # 超过阈值时返回退出码 1
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys

DEFAULT_ROOTS = ("api", "core", "engines", "integrations", "llm", "repositories", "services")


def iter_python_files(roots: tuple[str, ...], include_tests: bool) -> list[pathlib.Path]:
    base = pathlib.Path(__file__).resolve().parent.parent
    files: list[pathlib.Path] = []
    for root in roots:
        files.extend(p for p in (base / root).rglob("*.py") if "__pycache__" not in str(p))
    if include_tests:
        files.extend(p for p in (base / "tests").rglob("*.py") if "__pycache__" not in str(p))
    return sorted(files)


def audit_file(path: pathlib.Path, min_lines: int) -> list[tuple[int, int, str, str]]:
    """返回 ``[(行数, 语句数, 位置, 函数名), ...]``，只含"零行内注释"的长函数。"""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    findings: list[tuple[int, int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = node.end_lineno or node.lineno
        length = end - node.lineno + 1
        if length < min_lines:
            continue
        # 函数体起点：跳过 def 行与装饰器，取第一条语句（通常是 docstring）。
        first_stmt = min(stmt.lineno for stmt in node.body)
        body = lines[first_stmt - 1 : end]
        comments = sum(1 for line in body if line.strip().startswith("#"))
        if comments:
            continue
        statements = sum(1 for line in body if line.strip() and not line.strip().startswith("#"))
        findings.append((length, statements, f"{path}:{node.lineno}", node.name))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="报告缺少行内注释的长函数")
    parser.add_argument("--min-lines", type=int, default=30, help="只统计行数不低于该值的函数")
    parser.add_argument("--top", type=int, default=25, help="最多展示多少条")
    parser.add_argument("--include-tests", action="store_true", help="同时审计 tests/")
    parser.add_argument(
        "--fail-over",
        type=int,
        default=0,
        help="命中数超过该值时返回非零退出码（0 表示不因此失败）",
    )
    args = parser.parse_args(argv)

    base = pathlib.Path(__file__).resolve().parent.parent
    rows: list[tuple[int, int, str, str]] = []
    for path in iter_python_files(DEFAULT_ROOTS, args.include_tests):
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:  # pragma: no cover - 仅当脚本被搬到别处时
            rel = path.as_posix()
        for length, statements, _loc, name in audit_file(path, args.min_lines):
            rows.append((length, statements, rel, name))

    rows.sort(reverse=True)
    print(f"函数体内 0 条注释、且 ≥{args.min_lines} 行的函数：{len(rows)} 个")
    if rows:
        print(f"{'行数':>5} {'语句':>5}  位置")
        for length, statements, loc, name in rows[: args.top]:
            print(f"{length:5d} {statements:5d}  {loc}::{name}")
        if len(rows) > args.top:
            print(f"... 其余 {len(rows) - args.top} 条用 --top 调整")
    print("\n提示：这是线索不是门禁，规范见 docs/commenting-guide.md")

    if args.fail_over and len(rows) > args.fail_over:
        print(f"\n[FAIL] 命中 {len(rows)} 个，超过阈值 {args.fail_over}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
