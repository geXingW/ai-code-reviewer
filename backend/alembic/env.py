"""Alembic environment configured for SQLAlchemy async migrations."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.db import Base
from app.models import *  # noqa: F403

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Return the migration database URL from the environment or Alembic config."""

    return os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    """Run migrations without creating an engine."""

    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations against an established synchronous connection."""

    # MySQL 兼容修复：alembic_version.version_num 默认是 VARCHAR(32)，
    # 但新版本号（如 0005_project_notification_channel）超过 32 字符，
    # 导致 1406 Data too long 错误。PG 自带 VARCHAR(32) 不限制长度，
    # 只有 MySQL 严格校验。这里在运行所有迁移前统一扩大到 255。
    if connection.dialect.name == "mysql":
        connection.execute(
            text("ALTER TABLE alembic_version MODIFY COLUMN version_num VARCHAR(255) NOT NULL")
        )

    # 开启类型与 server_default 对比，保证后续 autogenerate 能检测到模型类型变更。
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations through SQLAlchemy's async engine."""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
