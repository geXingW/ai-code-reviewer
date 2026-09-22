"""从 ORM 模型直接导出建库 SQL（替代 alembic 迁移）。

项目已移除 alembic，数据库结构变更流程改为：
1. 修改 ``models/`` 下的 SQLAlchemy 模型；
2. 运行本脚本重新生成 ``sql/schema-{dialect}.sql``；
3. 由运维在数据库上**手动执行** SQL（项目启动不再自动执行任何 DDL）。

用法（在 ``backend/`` 目录下）::

    python scripts/export_schema_sql.py                    # 导出全部方言
    python scripts/export_schema_sql.py --dialect mysql    # 只导出 MySQL

不依赖数据库连接：仅使用 SQLAlchemy 各方言的 DDL 编译器离线渲染
``Base.metadata``。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, create_mock_engine
from sqlalchemy.sql.ddl import BaseDDLElement

# 保证从 backend/ 目录外运行时也能导入包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import models  # noqa: E402,F401  # 导入即注册所有模型到 Base.metadata
from core.db import Base  # noqa: E402

_BACKEND_ROOT = Path(__file__).resolve().parent.parent

# dialect 名 -> 输出文件名后缀（与 SQLAlchemy URL 方言名一致）
_DIALECTS: dict[str, str] = {
    "mysql": "mysql+pymysql://",
    "postgresql": "postgresql+psycopg://",
}


def render_schema_sql(dialect: str) -> str:
    """Render the full DDL for ``Base.metadata`` targeting ``dialect``.

    Args:
        dialect: SQLAlchemy dialect name (``mysql`` or ``postgresql``).

    Returns:
        str: Complete ``CREATE TABLE`` / ``CREATE INDEX`` statements.

    Raises:
        ValueError: If the dialect is not supported.
    """

    if dialect not in _DIALECTS:
        msg = f"unsupported dialect: {dialect} (expected one of {sorted(_DIALECTS)})"
        raise ValueError(msg)

    statements: list[str] = []

    # 生成的 SQL 不包含外键约束（表间关联由应用层维护），且
    # 唯一约束不带 CONSTRAINT 命名前缀（保留唯一性语义，仅去掉关键字）：
    # 编译前移除 ForeignKeyConstraint、清空 UniqueConstraint 名称。
    # 仅影响本进程内的编译过程，不落地到任何真实数据库。
    for table in Base.metadata.tables.values():
        fks = [c for c in table.constraints if isinstance(c, ForeignKeyConstraint)]
        table.constraints.difference_update(fks)
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and constraint.name is not None:
                constraint.name = None
        for column in table.columns:
            column.foreign_keys.clear()

    def _collect(sql: BaseDDLElement, *_args: object, **_kwargs: object) -> None:
        statements.append(str(sql.compile(compile_kwargs={"literal_binds": True})).strip() + ";")

    # mock engine：只触发 DDL 编译，不建立真实连接
    engine = create_mock_engine(_DIALECTS[dialect], executor=_collect)
    Base.metadata.create_all(engine, checkfirst=False)

    return "\n\n".join(statements)


def export(dialects: list[str], output_dir: Path) -> list[Path]:
    """Export schema SQL files for the given dialects.

    Args:
        dialects: Dialect names to export.
        output_dir: Directory to write ``schema-{dialect}.sql`` into.

    Returns:
        list[Path]: Generated file paths.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for dialect in dialects:
        content = render_schema_sql(dialect)
        path = output_dir / f"schema-{dialect}.sql"
        header = (
            f"-- ai-code-reviewer 全量建库脚本（{dialect}）\n"
            "-- 由 scripts/export_schema_sql.py 从 models/ 自动生成，请勿手改；\n"
            "-- 模型变更后重新运行：python scripts/export_schema_sql.py\n"
            "-- 使用方式：数据库为空库时手动执行本文件，应用启动不再自动建表。\n"
            "-- 注意：所有建库脚本均不包含外键约束，表间关联由应用层维护。\n\n"
        )
        path.write_text(header + content + "\n", encoding="utf-8")
        written.append(path)
        print(f"exported: {path}")

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export full schema SQL from ORM models (no alembic).",
    )
    parser.add_argument(
        "--dialect",
        action="append",
        choices=sorted(_DIALECTS),
        dest="dialects",
        help="Dialect to export; repeatable (default: all dialects)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_BACKEND_ROOT / "sql",
        help="Output directory (default: backend/sql)",
    )
    args = parser.parse_args(argv)

    export(args.dialects or list(_DIALECTS), args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
