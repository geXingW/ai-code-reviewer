"""0011_commit_reviews 迁移 upgrade/downgrade 往返测试（stamp 基线）。

不从 base 跑全量迁移链：链上既有迁移 0008_global_settings 在 MySQL 8.0
严格模式下会因 ``TEXT column can't have a default value`` 失败（PG 不受
影响），属既有问题，与本迁移无关（见 PR body）。改为先用当前 models
（即 0011 之后的状态）create_all 并把 alembic_version 写到 0011，再单独
验证 0011 的 downgrade / upgrade 往返，精确覆盖本迁移。

校验列形态：upgrade 后 ``reviews.review_kind`` 存在且 NOT NULL、
``reviews.mr_iid`` nullable；downgrade 后两者回滚。

版本号不走 ``command.stamp``，而是直接写 alembic_version：env.py 在
MySQL 上会先 ``ALTER TABLE alembic_version``，DDL 的隐式提交使 SQLAlchemy
的事务状态过期，alembic 复用该事务且从不 commit，导致 stamp 对版本表的
写入（以及后续命令里 DDL 之后的版本表更新）被回滚丢失。直接写行绕开该
问题；downgrade 后同理需要把版本行校正到 0010，upgrade 才会真正执行
（版本行的丢失不影响 DDL 本身——MySQL DDL 自带隐式提交）。

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

_HEAD_REVISION = "0011_commit_reviews"
_PREV_REVISION = "0010_project_negative_prompts"


@pytest.mark.skipif(
    not _DATABASE_URL,
    reason="需要真实数据库（DATABASE_URL）验证迁移",
)
@pytest.mark.asyncio
async def test_migration_0011_upgrade_and_downgrade_roundtrip() -> None:
    """写版本基线 -> downgrade -1 -> 校验回滚 -> upgrade head -> 校验列形态。"""

    from alembic.config import Config

    from alembic import command

    async def set_version(version: str) -> None:
        """把 alembic_version 重置为单个指定版本行（幂等）。

        既用于在 create_all 后写入基线（等价于 ``command.stamp``，但绕开
        env.py 在 MySQL 上 DDL 隐式提交导致 stamp 写入被回滚的问题），也
        用于 downgrade 后校正版本行——MySQL 上 alembic 对版本表的更新同样
        会被回滚，版本停在 0011 会让随后的 upgrade head 变成 no-op。
        """
        eng = create_async_engine(_DATABASE_URL)
        async with eng.begin() as conn:
            await conn.execute(text("DELETE FROM alembic_version"))
            await conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": version},
            )
        await eng.dispose()

    # 清场：drop 全部表 + alembic_version，保证从干净状态开始。
    # 随后 create_all（= 当前 models 的 schema，即 0011 之后的状态），
    # alembic_version 预建为 VARCHAR(255)：asyncpg 会在驱动层强制 VARCHAR(32)
    # 长度（0005_project_notification_channel 等 19+ 字符版本号会截断报错），
    # env.py 只对 MySQL 做了扩宽，这里在 PG 上对齐同一处理。
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
        # 只执行 0011 的 downgrade（drop review_kind + mr_iid 恢复 NOT NULL）。
        await asyncio.to_thread(command.downgrade, cfg, "-1")

        columns = await fetch_review_columns()
        assert "review_kind" not in columns
        assert columns["mr_iid"]["nullable"] is False

        # 校正版本行到 0010，保证 upgrade head 确实重放 0011 的 upgrade。
        await set_version(_PREV_REVISION)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        columns = await fetch_review_columns()
        assert "review_kind" in columns
        assert columns["review_kind"]["nullable"] is False
        assert columns["mr_iid"]["nullable"] is True
    finally:
        # 清场还给其它测试：drop 全部表（conftest 的 fixture 会 create_all）。
        engine = create_async_engine(_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await engine.dispose()
