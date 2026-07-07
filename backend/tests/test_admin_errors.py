"""Tests for admin error-mapping helpers, focused on ``_commit_or_400``.

Covers the three branches of ``_commit_or_400``:
- IntegrityError → 409 Conflict, detail preserved (重名/外键冲突)。
- 其他 SQLAlchemyError → 500 Internal Server Error, detail 暴露根因异常类名。
- commit 成功 → 不抛异常、不 rollback。
"""

from __future__ import annotations

from typing import NoReturn

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.admin import _commit_or_400


class _CommitFailingSession:
    """极简 async session：commit 时抛出预设异常，用于验证 _commit_or_400 的错误映射。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.rolled_back = False

    async def commit(self) -> NoReturn:
        """模拟 commit 失败：抛出构造时传入的异常。"""

        raise self._exc

    async def rollback(self) -> None:
        """记录 rollback 调用，便于断言错误分支已回滚。"""

        self.rolled_back = True


class _CommitOkSession:
    """极简 async session：commit 成功，用于验证成功路径不应触发 rollback。"""

    def __init__(self) -> None:
        self.rolled_back = False

    async def commit(self) -> None:
        """模拟 commit 成功。"""

    async def rollback(self) -> None:
        """成功路径不应调用 rollback；记录以便断言。"""

        self.rolled_back = True


@pytest.mark.asyncio
async def test_commit_or_400_integrity_error_maps_to_409() -> None:
    """IntegrityError（唯一约束/外键冲突）应映射为 409 并保留传入 detail。"""

    exc = IntegrityError(
        "INSERT INTO providers",
        {},
        Exception("duplicate key value violates unique constraint"),
    )
    session = _CommitFailingSession(exc)

    with pytest.raises(HTTPException) as info:
        await _commit_or_400(session, "Provider already exists")

    assert info.value.status_code == 409
    assert info.value.detail == "Provider already exists"
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_commit_or_400_other_sqlalchemy_error_maps_to_500() -> None:
    """非 IntegrityError 的 SQLAlchemyError（如加密失败）应映射为 500 并暴露根因类名。"""

    exc = OperationalError(
        "INSERT INTO providers",
        {},
        Exception("encryption configuration error"),
    )
    session = _CommitFailingSession(exc)

    with pytest.raises(HTTPException) as info:
        await _commit_or_400(session, "Provider create failed")

    assert info.value.status_code == 500
    detail = info.value.detail
    assert isinstance(detail, str)
    assert "Provider create failed" in detail
    assert "OperationalError" in detail
    assert "encryption configuration error" in detail
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_commit_or_400_success_does_not_raise() -> None:
    """commit 成功时不应抛异常、不应 rollback。"""

    session = _CommitOkSession()

    await _commit_or_400(session, "Provider create failed")

    assert session.rolled_back is False
