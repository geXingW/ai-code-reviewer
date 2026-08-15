"""SQLAlchemy model for per-project GitLab↔DingTalk user mappings."""

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base, TimestampMixin

if TYPE_CHECKING:
    from models.project import Project


class UserMapping(Base, TimestampMixin):
    """GitLab 用户名 ↔ 钉钉手机号 的映射关系，项目级隔离。

    用于 Review 完成通知时 @ MR 创建人：``gitlab_username`` 来自 webhook 顶层
    ``user`` 对象，``dingtalk_mobile`` 是该用户在钉钉绑定的手机号（钉钉 @ 人
    用 ``atMobiles`` 即可高亮）。查不到映射时通知照发、只是不 @ 人。
    """

    __tablename__ = "user_mappings"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "gitlab_username",
            name="uniq_user_mappings_project_gitlab",
        ),
    )

    # 主键 UUID 由 Python 层生成，不依赖 PG 的 gen_random_uuid()，保证 MySQL 也可用。
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    gitlab_username: Mapped[str] = mapped_column(String(255), nullable=False)
    dingtalk_mobile: Mapped[str] = mapped_column(String(32), nullable=False)
    dingtalk_userid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    project: Mapped["Project"] = relationship(
        back_populates="user_mappings",
        lazy="selectin",
    )
