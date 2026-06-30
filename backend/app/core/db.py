"""Database engine, sessions, base model, and health checks."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import DateTime, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


class TimestampMixin:
    """Reusable created/updated timestamp columns for ORM models."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


settings = get_settings()
engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for FastAPI dependencies.

    Yields:
        AsyncSession: SQLAlchemy async session.
    """

    async with AsyncSessionLocal() as session:
        yield session


async def ping_database() -> bool:
    """Check whether PostgreSQL accepts a simple query.

    Returns:
        True when the database responds successfully, otherwise False.
    """

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError):
        return False
    return True


DbSession = Annotated[AsyncSession, Depends(get_db)]
