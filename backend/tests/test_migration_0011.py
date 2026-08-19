"""0011_commit_reviews 迁移在真实 PG 上的 upgrade/downgrade 往返测试。

同时验证 0011 落地后的列形态：``reviews.review_kind`` NOT NULL、
``reviews.mr_iid`` nullable。

alembic 的 env.py 用 ``asyncio.run`` 驱动在线迁移，不能在 pytest-asyncio 的
事件循环里直接调用，因此通过 ``asyncio.to_thread`` 在工作线程执行 command。
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


@pytest.mark.skipif(
    not _DATABASE_URL,
    reason="需要真实数据库（DATABASE_URL）验证迁移",
)
@pytest.mark.asyncio
async def test_migration_0011_upgrade_and_downgrade_roundtrip() -> None:
    """upgrade head -> 校验列 -> downgrade -1 -> 校验回滚 -> 恢复 head。"""

    from alembic.config import Config

    from alembic import command

    # 清场：drop 全部表 + alembic_version，保证从 base 开始跑迁移。
    # alembic_version 预建为 VARCHAR(255)：asyncpg 会在驱动层强制 VARCHAR(32)
    # 长度（0005_project_notification_channel 等 19+ 字符版本号会截断报错），
    # env.py 只对 MySQL 做了扩宽，这里在 PG 上对齐同一处理。
    engine = create_async_engine(_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(255) NOT NULL)")
        )
    await engine.dispose()

    # 用不带 ini 文件的 Config：env.py 只在 config_file_name 非 None 时调
    # logging fileConfig，后者会替换 root logger 的 handlers，把 pytest caplog
    # 的 handler 摘掉，导致同进程后续用例的日志断言全部失效。
    cfg = Config()
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _DATABASE_URL)

    async def fetch_review_columns() -> dict[str, dict[str, object]]:
        eng = create_async_engine(_DATABASE_URL)
        try:
            async with eng.connect() as conn:

                def _columns(connection: object) -> dict[str, dict[str, object]]:
                    inspector = inspect(connection)  # type: ignore[arg-type]
                    return {
                        column["name"]: column
                        for column in inspector.get_columns("reviews")
                    }

                return await conn.run_sync(_columns)
        finally:
            await eng.dispose()

    try:
        await asyncio.to_thread(command.upgrade, cfg, "head")

        columns = await fetch_review_columns()
        assert "review_kind" in columns
        assert columns["review_kind"]["nullable"] is False
        assert columns["mr_iid"]["nullable"] is True

        await asyncio.to_thread(command.downgrade, cfg, "-1")

        columns = await fetch_review_columns()
        assert "review_kind" not in columns
        assert columns["mr_iid"]["nullable"] is False

        # 恢复到 head，保证后续用例 / 手工诊断拿到的是最新 schema。
        await asyncio.to_thread(command.upgrade, cfg, "head")
    finally:
        # 清场还给其它测试：drop 全部表（conftest 的 fixture 会 create_all）。
        engine = create_async_engine(_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await engine.dispose()
