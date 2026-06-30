"""Seed the database with initial engines, block policy templates, and rules."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import AsyncSessionLocal, engine
from app.models.engine import Engine
from app.models.project_block_policy import ProjectBlockPolicy
from app.models.rule import Rule


async def seed_engine() -> None:
    """Insert the default built-in review engine if it does not exist."""

    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(Engine).where(Engine.name == "llm-direct"))
        if existing is None:
            session.add(
                Engine(
                    name="llm-direct",
                    engine_type="builtin",
                    config={"max_context_tokens": 128000},
                )
            )
        await session.commit()


async def seed_block_policies() -> None:
    """Insert global default block policy templates if they do not exist."""

    policies = [
        {
            "priority": 1,
            "branch_pattern": "master",
            "block_severity": "BLOCKER",
            "require_all_resolved": True,
        },
        {
            "priority": 2,
            "branch_pattern": "release/*",
            "block_severity": "BLOCKER",
            "require_all_resolved": False,
        },
        {
            "priority": 3,
            "branch_pattern": "hotfix/*",
            "block_severity": "BLOCKER",
            "require_all_resolved": False,
        },
        {
            "priority": 99,
            "branch_pattern": "*",
            "block_severity": "NONE",
            "require_all_resolved": False,
        },
    ]

    async with AsyncSessionLocal() as session:
        for policy in policies:
            existing = await session.scalar(
                select(ProjectBlockPolicy).where(
                    ProjectBlockPolicy.project_id.is_(None),
                    ProjectBlockPolicy.branch_pattern == policy["branch_pattern"],
                    ProjectBlockPolicy.priority == policy["priority"],
                )
            )
            if existing is None:
                session.add(ProjectBlockPolicy(project_id=None, **policy))
        await session.commit()


async def seed_rules() -> None:
    """Insert baseline review rules if they do not exist."""

    rules = [
        {
            "rule_id": "general.sql-injection",
            "title": "SQL 注入风险",
            "prompt_snippet": "检查代码中是否存在 SQL 注入风险，尤其是字符串拼接构造 SQL 的场景。",
            "severity_default": "BLOCKER",
            "languages": ["java", "python", "go", "javascript"],
        },
        {
            "rule_id": "general.hardcoded-secret",
            "title": "硬编码密钥/密码",
            "prompt_snippet": "检查代码中是否硬编码密钥、密码、Token 或其他敏感凭据。",
            "severity_default": "BLOCKER",
            "languages": ["*"],
        },
        {
            "rule_id": "java.null-safety",
            "title": "潜在 NPE",
            "prompt_snippet": "检查 Java/Kotlin 代码中可能触发空指针异常的访问路径。",
            "severity_default": "WARNING",
            "languages": ["java", "kotlin"],
        },
        {
            "rule_id": "python.exception-handling",
            "title": "异常处理不当",
            "prompt_snippet": "检查 Python 代码中过宽、吞掉或缺失上下文的异常处理。",
            "severity_default": "WARNING",
            "languages": ["python"],
        },
    ]

    async with AsyncSessionLocal() as session:
        for rule in rules:
            existing = await session.scalar(select(Rule).where(Rule.rule_id == rule["rule_id"]))
            if existing is None:
                session.add(Rule(path_patterns=[], enabled=True, **rule))
        await session.commit()


async def main() -> None:
    """Run all idempotent seed operations."""

    await seed_engine()
    await seed_block_policies()
    await seed_rules()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
