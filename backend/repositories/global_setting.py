"""Repository for global key-value settings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.global_setting import GlobalSetting


class GlobalSettingRepository:
    """Data access for the ``global_settings`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str) -> GlobalSetting | None:
        """Fetch a setting by key, or ``None`` if not set."""

        result = await self._session.execute(
            select(GlobalSetting).where(GlobalSetting.key == key),
        )
        return result.scalar_one_or_none()

    async def get_value(self, key: str, default: str = "") -> str:
        """Fetch a setting's value, or ``default`` if not set."""

        setting = await self.get(key)
        return setting.value if setting is not None else default

    async def set_value(self, key: str, value: str) -> GlobalSetting:
        """Insert or update a setting (upsert pattern)."""

        existing = await self.get(key)
        if existing is not None:
            existing.value = value
            await self._session.flush()
            return existing

        setting = GlobalSetting(key=key, value=value)
        self._session.add(setting)
        await self._session.flush()
        return setting
