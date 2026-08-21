"""0012_project_commit_review 迁移 upgrade/downgrade 往返测试（stamp 基线）。

与 test_migration_0011 同一模式：不从 base 跑全量迁移链，先用当前 models
create_all 并把 alembic_version 写到 0012，再单独验证 0012 的
downgrade / upgrade 往返，精确覆盖本迁移。

校验列形态：upgrade 后 ``projects.commit_review_enabled`` /
``commit_review_max_per_push`` 存在且 NOT NULL；downgrade 后两列消失。

版本号不走 ``command.stamp``，而是直接写 alembic_version（MySQL 上 env.py
的 DDL 隐式提交会把 stamp 的版本表写入回滚掉，见 test_migration_0011 的
说明）；downgrade 后同理需要把版本行校正到 0011，upgrade 才会真正执行。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from core.db import Base

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_DATABASE_URL = os.getenv("DATABASE_URL", "")

_HEAD_REVISION = "0012_project_commit_review"
_PREV_REVISION = "0011_commit_reviews"


@pytest.mark.skipif(
    not _DATABASE_URL,
    reason="需要真实数据库（DATABASE_URL）验证迁移",
)
@pytest.mark.asyncio
async def test_migration_0012_upgrade_and_downgrade_roundtrip() -> None:
    """写版本基线 -> downgrade -1 -> 校验回滚 -> upgrade head -> 校验列形态。"""

    from alembic.config import Config

    from alembic import command

    async def set_version(version: str) -> None:
        """把 alembic_version 重置为单个指定版本行（幂等）。"""

        eng = create_async_engine(_DATABASE_URL)
        async with eng.begin() as conn:
            await conn.execute(text("DELETE FROM alembic_version"))
            await conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": version},
            )
        await eng.dispose()

    # 清场：drop 全部表 + alembic_version，随后 create_all（= 当前 models 的
    # schema，即 0012 之后的状态）；alembic_version 预建 VARCHAR(255) 对齐
    # asyncpg 的长度限制（见 test_migration_0011 的说明）。
    engine = create_async_engine(_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(255) NOT NULL)")
        )
    await engine.dispose()
    await set_version(_HEAD_REVISION)

    # 用不带 ini 文件的 Config：避免 env.py 的 logging fileConfig 摘掉
    # pytest caplog 的 handler（见 test_migration_0011 的说明）。
    cfg = Config()
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _DATABASE_URL)

    async def project_columns() -> dict[str, dict[str, object]]:
        """取 projects 表的 {列名: 列信息} 映射（含 nullable / default）。"""

        eng = create_async_engine(_DATABASE_URL)
        try:
            async with eng.connect() as conn:

                def _columns(connection: object) -> dict[str, dict[str, object]]:
                    inspector = inspect(connection)  # type: ignore[arg-type]
                    return {
                        column["name"]: column
                        for column in inspector.get_columns("projects")
                    }

                return await conn.run_sync(_columns)
        finally:
            await eng.dispose()

    try:
        # 只执行 0012 的 downgrade（drop 两个项目级 commit 审查配置列）。
        await asyncio.to_thread(command.downgrade, cfg, "-1")

        columns = await project_columns()
        assert "commit_review_enabled" not in columns
        assert "commit_review_max_per_push" not in columns

        # 校正版本行到 0011，保证 upgrade 确实重放 0012 的 upgrade。
        await set_version(_PREV_REVISION)

        await asyncio.to_thread(command.upgrade, cfg, _HEAD_REVISION)

        columns = await project_columns()
        assert columns["commit_review_enabled"]["nullable"] is False
        assert columns["commit_review_max_per_push"]["nullable"] is False
    finally:
        # 清场还给其它测试：drop 全部表（conftest 的 fixture 会 create_all）。
        engine = create_async_engine(_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await engine.dispose()
