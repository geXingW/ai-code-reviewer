"""Generate release SQL artifacts from Alembic migrations.

Produces:
- ``sql/VERSION`` – the current Alembic head revision string
- ``sql/schema-full.sql`` – full DDL from empty database to head
- ``sql/migrations/XXXX_*.sql`` – one incremental SQL file per migration

The script uses Alembic's offline mode (``--sql``) so no live database
connection is required. It works by calling Alembic's Python API directly
and capturing the generated SQL.

Usage::

    python scripts/generate_release_sql.py [--output-dir sql]

Run from the ``backend/`` directory (where ``alembic.ini`` lives).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from alembic.config import Config

from alembic import command


def _capture_offline_sql(alembic_cfg: Config, revision_range: str) -> str:
    """Run Alembic in offline mode and return the generated SQL string.

    Args:
        alembic_cfg: Alembic configuration instance.
        revision_range: Passed to ``command.upgrade`` (e.g. ``"head"`` or
            ``"0001:0002"``).

    Returns:
        str: The SQL output produced by Alembic's offline mode.
    """

    # 用 StringIO 捕获 Alembic 的 SQL 输出（offline 模式会 print 到 stdout）。
    from io import StringIO

    buffer = StringIO()
    original_stdout = sys.stdout
    sys.stdout = buffer
    try:
        command.upgrade(alembic_cfg, revision_range, sql=True)
    finally:
        sys.stdout = original_stdout
    return buffer.getvalue()


def _get_revision_order(alembic_cfg: Config) -> list[tuple[str, str]]:
    """Return the ordered list of (revision, down_revision) tuples.

    Walks the full migration DAG using topological sort so merge revisions
    (multiple ``down_revision``) and multi-head histories are handled correctly.
    Returns the list from oldest to newest (base → head).

    For each entry, ``down_revision`` is the primary parent used for
    incremental SQL generation: for regular linear migrations it is the
    single parent; for merge revisions it is the first parent in the tuple.
    """

    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_cfg)
    heads = script.get_heads()
    if not heads:
        return []

    # 收集所有 revision: DFS 从所有 head 倒推到 base
    all_revs: dict[str, list[str]] = {}  # revision -> list of down revisions
    stack: list[str] = list(heads)
    visited: set[str] = set()

    while stack:
        rev = stack.pop()
        if rev in visited:
            continue
        visited.add(rev)
        scr = script.get_revision(rev)
        down = scr.down_revision
        if down is None:
            parents: list[str] = []
        elif isinstance(down, (tuple, list)):
            parents = [str(d) for d in down]
        else:
            parents = [str(down)]
        all_revs[rev] = parents
        for p in parents:
            if p not in visited:
                stack.append(p)

    # 拓扑排序（Kahn 算法）：base → head
    in_degree = {rev: len(parents) for rev, parents in all_revs.items()}
    # children 映射：parent -> [children]
    children: dict[str, list[str]] = {rev: [] for rev in all_revs}
    for rev, parents in all_revs.items():
        for p in parents:
            if p not in children:
                children[p] = []
            children[p].append(rev)

    # 起点：in_degree 为 0 的节点（base revision，即 down_revision 为 None 的）
    queue = [rev for rev, deg in in_degree.items() if deg == 0]
    queue.sort()  # 稳定排序
    topo_order: list[str] = []
    while queue:
        rev = queue.pop(0)
        topo_order.append(rev)
        for child in children.get(rev, []):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)
        queue.sort()  # 稳定排序

    # 构造结果：(revision, primary_down_revision)
    result: list[tuple[str, str]] = []
    for rev in topo_order:
        parents = all_revs[rev]
        primary_down = parents[0] if parents else "base"
        result.append((rev, primary_down))

    return result


def _slug_for_filename(revision: str) -> str:
    """Convert a revision identifier to a filesystem-safe filename stem.

    Revision strings like ``"0001_initial_schema"`` are already safe; we
    just strip whitespace and ensure no path separators.
    """

    safe = re.sub(r"[^\w\-.]", "_", revision.strip())
    return safe or "migration"


def generate(alembic_ini: Path, output_dir: Path) -> dict[str, Path]:
    """Generate all release SQL artifacts.

    Args:
        alembic_ini: Path to ``alembic.ini``.
        output_dir: Directory to write SQL files into.

    Returns:
        dict mapping artifact kind to the generated file path.
    """

    if not alembic_ini.exists():
        msg = f"alembic.ini not found at {alembic_ini}"
        raise FileNotFoundError(msg)

    alembic_cfg = Config(str(alembic_ini))

    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)
    migrations_dir = output_dir / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)

    generated: dict[str, Path] = {}

    # --- 1. VERSION 文件 ---
    revisions = _get_revision_order(alembic_cfg)
    if not revisions:
        msg = "No migrations found; cannot determine head revision."
        raise RuntimeError(msg)
    head_rev = revisions[-1][0]
    version_file = output_dir / "VERSION"
    version_file.write_text(f"{head_rev}\n", encoding="utf-8")
    generated["version"] = version_file

    # --- 2. 全量 schema-full.sql ---
    full_sql = _capture_offline_sql(alembic_cfg, "head")
    full_file = output_dir / "schema-full.sql"
    full_file.write_text(full_sql, encoding="utf-8")
    generated["schema_full"] = full_file

    # --- 3. 增量 SQL（每个 migration 一个文件） ---
    for rev, down_rev in revisions:
        rev_range = f"{down_rev}:{rev}"
        inc_sql = _capture_offline_sql(alembic_cfg, rev_range)
        filename = f"{_slug_for_filename(rev)}.sql"
        inc_file = migrations_dir / filename
        inc_file.write_text(inc_sql, encoding="utf-8")
        generated[f"migration_{rev}"] = inc_file

    return generated


def main() -> None:
    """CLI entry point."""

    parser = argparse.ArgumentParser(
        description="Generate release SQL artifacts from Alembic migrations.",
    )
    parser.add_argument(
        "--alembic-ini",
        default="alembic.ini",
        help="Path to alembic.ini (default: ./alembic.ini)",
    )
    parser.add_argument(
        "--output-dir",
        default="sql",
        help="Output directory (default: ./sql)",
    )
    args = parser.parse_args()

    alembic_ini = Path(args.alembic_ini).resolve()
    output_dir = Path(args.output_dir).resolve()

    generated = generate(alembic_ini, output_dir)

    print(f"Generated {len(generated)} SQL artifacts in {output_dir}")
    for key, path in sorted(generated.items()):
        rel = path.relative_to(output_dir.parent) if output_dir.parent in path.parents else path
        print(f"  {key}: {rel}")


if __name__ == "__main__":
    main()
