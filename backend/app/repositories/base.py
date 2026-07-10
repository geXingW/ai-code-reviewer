"""Repository 通用基类。"""

from __future__ import annotations

from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """通用仓储基类：封装最常见的读写操作。

    子类必须重写 ``model`` 类属性指向具体 ORM 模型。所有写方法只做 ``flush()``，
    是否 ``commit()`` 由上层路由/服务在合适时机决定，避免嵌套事务或提前落盘。
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """暴露底层会话，仅供需要在同一事务中协作的场景使用。"""

        return self._session

    async def get(self, id_: UUID | str) -> ModelT | None:
        """按主键取单条记录，不存在返回 ``None``。"""

        return await self._session.get(self.model, id_)

    async def list_all(self, *, order_by: ColumnElement[object] | None = None) -> list[ModelT]:
        """列出所有记录，可传入 ORM 列做排序。"""

        stmt = select(self.model)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, obj: ModelT, *, flush: bool = True) -> ModelT:
        """挂载新对象到会话；默认立即 ``flush()`` 触发数据库约束校验。"""

        self._session.add(obj)
        if flush:
            await self._session.flush()
        return obj

    async def delete(self, obj: ModelT, *, flush: bool = True) -> None:
        """删除对象并 flush。"""

        await self._session.delete(obj)
        if flush:
            await self._session.flush()

    async def count(self) -> int:
        """统计表中总行数。"""

        stmt = select(func.count()).select_from(self.model)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def flush(self) -> None:
        """显式触发 flush，等价于 ``session.flush()``。"""

        await self._session.flush()

    async def refresh(self, obj: ModelT) -> ModelT:
        """从数据库刷新对象的关系字段/默认值。"""

        await self._session.refresh(obj)
        return obj
