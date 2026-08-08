"""Compatibility exports for SQLAlchemy base classes used by models."""

from core.db import Base, TimestampMixin

__all__ = ["Base", "TimestampMixin"]
