"""Compatibility exports for SQLAlchemy base classes used by models."""

from app.core.db import Base, TimestampMixin

__all__ = ["Base", "TimestampMixin"]
