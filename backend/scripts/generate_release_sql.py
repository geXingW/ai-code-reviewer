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

    Walks from head down to base using Alembic's script directory.
    Returns the list from oldest to newest.
    """

    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_cfg)
    heads = script.get_heads()
    if not heads:
        return []

    # 从 head 倒推到 base，再反转得到正序。
    revisions_reverse: list[tuple[str, str | None]] = []
    current: str | None = heads[0] if isinstance(heads[0], str) else heads[0][0]
    while current:
        scr = script.get_revision(current)
        down = scr.down_revision
        if isinstance(down, (tuple, list)):
            down = down[0] if down else None
        revisions_reverse.append((str(scr.revision), str(down) if down else None))
        current = down  # type: ignore[assignment]

    revisions_reverse.reverse()
    # 过滤掉 base 之前的空节点（第一个元素 down_revision 为 None）。
    return [(rev, down or "base") for rev, down in revisions_reverse]


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
