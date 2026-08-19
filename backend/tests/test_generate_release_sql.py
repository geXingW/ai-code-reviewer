"""Tests for ``scripts/generate_release_sql.py``.

Covers the multi-head / merge-migration case that broke release packaging
when two 0008 migrations landed from separate branches.
"""

from __future__ import annotations

from pathlib import Path


def test_get_revision_order_single_head_linear(tmp_path: Path) -> None:
    """Linear history (no merge) produces a straightforward oldest→newest list."""

    from alembic.config import Config

    from scripts.generate_release_sql import _get_revision_order

    cfg = Config("alembic.ini")
    result = _get_revision_order(cfg)

    # 至少有 base + 0001 + ... 若干迁移
    assert len(result) >= 2
    # 第一条一定从 base 开始
    assert result[0][1] == "base"
    # 最后一条是 head
    assert result[-1][0].startswith("0010_")

    # 每一条的 down_revision 都是前面已经出现过的 revision 或 base
    seen = {"base"}
    for rev, down in result:
        assert down in seen, f"{rev} 引用了未出现的 down_revision={down}"
        seen.add(rev)


def test_get_revision_order_contains_both_0008_branches(tmp_path: Path) -> None:
    """Both 0008 migrations must appear (they come from different branches)."""

    from alembic.config import Config

    from scripts.generate_release_sql import _get_revision_order

    cfg = Config("alembic.ini")
    result = _get_revision_order(cfg)
    revs = [rev for rev, _ in result]

    assert "0008_global_settings" in revs
    assert "0008_user_mappings" in revs
    # merge revision 在两个 0008 之后
    idx_gs = revs.index("0008_global_settings")
    idx_um = revs.index("0008_user_mappings")
    idx_merge = revs.index("0009_merge_0008_heads")
    assert idx_merge > idx_gs
    assert idx_merge > idx_um


def test_generate_produces_expected_artifacts(tmp_path: Path) -> None:
    """``generate()`` produces VERSION, schema-full.sql, and per-migration SQL."""

    from scripts.generate_release_sql import generate

    output_dir = tmp_path / "sql"
    result = generate(Path("alembic.ini"), output_dir)

    assert "version" in result
    assert "schema_full" in result
    # 至少 10 个迁移（0001-0010）
    migration_keys = [k for k in result if k.startswith("migration_")]
    assert len(migration_keys) >= 10

    # VERSION 文件内容是 head revision
    version_text = result["version"].read_text().strip()
    assert version_text == "0010_project_negative_prompts"

    # schema-full.sql 里必须包含两张 0008 的表与 0010 的项目级负样本提示词表
    schema_sql = result["schema_full"].read_text()
    assert "CREATE TABLE global_settings" in schema_sql
    assert "CREATE TABLE user_mappings" in schema_sql
    assert "CREATE TABLE project_negative_prompts" in schema_sql
