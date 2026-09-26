"""schema 导出脚本方言编译的回归测试。

export_schema_sql 此前在 _collect 里调用 ``sql.compile()`` 时未传 dialect，
SQLAlchemy 退回默认方言渲染 DDL，导致：
1. MySQL 保留字（如 global_settings 的 ``key`` 列）不加反引号，
   schema-mysql.sql 无法执行（You have an error in your SQL syntax ... near 'VARCHAR(255) ...'）；
2. PostgreSQL 文件被渲染成 DATETIME / CHAR(32) 等非 PG 类型，同样不可执行。

修复后按目标方言编译：MySQL 输出 `` `key` ``，PostgreSQL 输出原生 UUID /
TIMESTAMP (WITH|WITHOUT) TIME ZONE。测试传入 ``Base.metadata.copy()``，
避免 render 过程对全局元数据的原地修改（移除外键约束）影响其他用例。
"""

from __future__ import annotations

from sqlalchemy import MetaData

from core.db import Base
from scripts.export_schema_sql import render_schema_sql


def _isolated_metadata() -> MetaData:
    """复制全局元数据，隔离 render_schema_sql 对约束的原地修改。"""

    copied = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(copied)
    return copied


def test_mysql_schema_quotes_reserved_word_key() -> None:
    """MySQL 方言下 global_settings.key 必须加反引号。"""

    sql = render_schema_sql("mysql", metadata=_isolated_metadata())
    assert "`key` VARCHAR(255) NOT NULL" in sql
    assert "PRIMARY KEY (`key`)" in sql
    assert "\tkey VARCHAR" not in sql


def test_mysql_schema_column_attribute_order_is_valid() -> None:
    """MySQL 要求 [NOT NULL] 在 [DEFAULT] 之前，默认方言渲染顺序相反。"""

    sql = render_schema_sql("mysql", metadata=_isolated_metadata())
    assert "BOOL NOT NULL DEFAULT true" in sql
    assert "DEFAULT true NOT NULL" not in sql


def test_postgresql_schema_uses_native_types() -> None:
    """PostgreSQL 方言应输出 UUID / TIMESTAMP，而非 DATETIME / CHAR(32)。"""

    sql = render_schema_sql("postgresql", metadata=_isolated_metadata())
    assert "UUID NOT NULL" in sql
    assert "TIMESTAMP WITH TIME ZONE NOT NULL" in sql
    assert "DATETIME" not in sql
    # 注意 VARCHAR(32) 合法，要排除的是独立的 CHAR(32)（UUID 列被渲染成 CHAR）
    assert " CHAR(32)" not in sql
