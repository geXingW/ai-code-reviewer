"""Generate release SQL artifacts from Alembic migrations.

Produces:
- ``sql/VERSION`` – the current Alembic head revision string
- ``sql/schema-full.sql`` – full DDL from empty database to head
- ``sql/migrations/XXXX_*.sql`` – one incremental SQL file per migration

The script uses Alembic's offline mode (``--sql``) so no live database
connection is required. It works by calling Alembic's Python API directly
and capturing the generated SQL.

Dialect is controlled by ``--dialect`` (default: ``mysql``, matching the
deployment target). Pass ``postgresql`` to generate PG-flavored SQL.

Usage::

    python scripts/generate_release_sql.py [--output-dir sql] [--dialect mysql]

Run from the ``backend/`` directory (where ``alembic.ini`` lives).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from alembic.config import Config

from alembic import command


def _dialect_url(dialect: str) -> str:
    """Return a dummy DSN for the given dialect for offline SQL generation.

    Offline mode only needs the dialect part of the URL; the host/user/password
    are irrelevant because no real connection is made.
    """

    if dialect == "mysql":
        return "mysql+aiomysql://dummy:dummy@localhost:3306/dummy"
    if dialect in {"postgresql", "postgres", "pg"}:
        return "postgresql+asyncpg://dummy:dummy@localhost:5432/dummy"
    msg = f"Unsupported dialect: {dialect} (expected 'mysql' or 'postgresql')"
    raise ValueError(msg)


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

    Uses topological sort (Kahn's algorithm) over the migration DAG so it
    correctly handles multiple heads and merge revisions.  Revisions with
    the same down_revision are ordered by revision id for determinism.

    Returns the list from oldest to newest.  Each ``down_revision`` is
    either a parent revision string or ``"base"`` for the first migration.
    """

    from collections import deque

    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_cfg)

    # 收集所有 revision 及其 down_revision（可能是单个或 tuple）
    all_revs: dict[str, list[str]] = {}  # rev -> list of down_revisions
    for scr in script.walk_revisions():
        down = scr.down_revision
        if down is None:
            all_revs[str(scr.revision)] = []
        elif isinstance(down, (tuple, list)):
            all_revs[str(scr.revision)] = [str(d) for d in down]
        else:
            all_revs[str(scr.revision)] = [str(down)]

    # 计算入度（每个 revision 有多少个 children 依赖它）
    out_degree: dict[str, int] = {rev: 0 for rev in all_revs}
    children: dict[str, list[str]] = {rev: [] for rev in all_revs}
    for rev, downs in all_revs.items():
        for d in downs:
            if d in children:
                children[d].append(rev)
                out_degree[rev] = out_degree.get(rev, 0)
    # 实际上入度 = 该 rev 的 down_revision 数量
    in_degree: dict[str, int] = {rev: len(downs) for rev, downs in all_revs.items()}

    # Kahn 算法：从入度为 0 的节点开始（即没有 down_revision 的初始 migration）
    queue: deque[str] = deque(
        sorted(rev for rev, deg in in_degree.items() if deg == 0)
    )
    result: list[str] = []
    while queue:
        rev = queue.popleft()
        result.append(rev)
        # 找到所有以 rev 为父节点的子 migration
        for child, downs in all_revs.items():
            if rev in downs:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    # 插入时保持排序，确保确定性
                    insert_pos = 0
                    while insert_pos < len(queue) and queue[insert_pos] < child:
                        insert_pos += 1
                    queue.insert(insert_pos, child)

    if len(result) != len(all_revs):
        missing = set(all_revs) - set(result)
        msg = f"Migration DAG has cycles or unreachable revisions: {missing}"
        raise RuntimeError(msg)

    # 构造 (revision, down_rev) 对，down_rev 用第一个父节点（base 用 "base"）
    ordered: list[tuple[str, str]] = []
    for rev in result:
        downs = all_revs[rev]
        down_repr = downs[0] if downs else "base"
        ordered.append((rev, down_repr))
    return ordered


def _slug_for_filename(revision: str) -> str:
    """Convert a revision identifier to a filesystem-safe filename stem.

    Revision strings like ``"0001_initial_schema"`` are already safe; we
    just strip whitespace and ensure no path separators.
    """

    safe = re.sub(r"[^\w\-.]", "_", revision.strip())
    return safe or "migration"


def generate(
    alembic_ini: Path,
    output_dir: Path,
    *,
    dialect: str = "mysql",
) -> dict[str, Path]:
    """Generate all release SQL artifacts.

    Args:
        alembic_ini: Path to ``alembic.ini``.
        output_dir: Directory to write SQL files into.
        dialect: Target SQL dialect (``"mysql"`` or ``"postgresql"``).
            Defaults to ``"mysql"`` matching the deployment target.

    Returns:
        dict mapping artifact kind to the generated file path.
    """

    if not alembic_ini.exists():
        msg = f"alembic.ini not found at {alembic_ini}"
        raise FileNotFoundError(msg)

    alembic_cfg = Config(str(alembic_ini))

    # 覆盖 sqlalchemy.url 以切换方言。offline 模式不需要真实连接，
    # 只需要 URL 中的 dialect 部分来决定生成 SQL 的语法。
    alembic_cfg.set_main_option("sqlalchemy.url", _dialect_url(dialect))

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
    # 多 head 时 VERSION 每行一个 head（按拓扑排序末尾的若干个）
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_cfg)
    heads = sorted(script.get_heads())
    version_file = output_dir / "VERSION"
    version_file.write_text("\n".join(heads) + "\n", encoding="utf-8")
    generated["version"] = version_file

    # --- 2. 全量 schema-full.sql ---
    # 用 "heads" 而非 "head"，支持多 head（分支未合并时也能生成全量 SQL）
    full_sql = _capture_offline_sql(alembic_cfg, "heads")
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
    parser.add_argument(
        "--dialect",
        default="mysql",
        choices=["mysql", "postgresql"],
        help="Target SQL dialect (default: mysql)",
    )
    args = parser.parse_args()

    alembic_ini = Path(args.alembic_ini).resolve()
    output_dir = Path(args.output_dir).resolve()

    generated = generate(alembic_ini, output_dir, dialect=args.dialect)

    print(f"Generated {len(generated)} SQL artifacts in {output_dir}")
    for key, path in sorted(generated.items()):
        rel = path.relative_to(output_dir.parent) if output_dir.parent in path.parents else path
        print(f"  {key}: {rel}")


if __name__ == "__main__":
    main()
