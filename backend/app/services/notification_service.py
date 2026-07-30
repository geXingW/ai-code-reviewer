"""Review 完成通知推送服务。

按项目配置的通知渠道，把 Review 结果（成功 / 引擎异常）推送到对应渠道。当前优先
支持钉钉；其它 ``channel_type`` 暂时跳过。**推送失败一律 fail-silent**（记 warning
日志，不抛异常），避免影响 Review 主流程。

与 :class:`ReviewOrchestrator` 一致，本服务通过 ``session_factory`` 在每次推送时
自行开启 session 查询渠道，构造期不持有会话，便于在 orchestrator 内复用同一
sessionmaker。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.integrations.dingtalk.client import DingTalkClient
from app.models.project_notification_channel import ProjectNotificationChannel
from app.repositories.project import ProjectRepository
from app.repositories.project_notification_channel import (
    ProjectNotificationChannelRepository,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    # 与 ReviewOrchestrator.SessionFactory 同语义：返回 AsyncSession 上下文管理器的零参可调用。
    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

logger = logging.getLogger(__name__)


class NotificationService:
    """按项目渠道推送 Review 完成通知。

    Args:
        session_factory: 应用级 async_sessionmaker；为 ``None`` 时跳过渠道查询（MVP 兼容）。
        client_factory: 钉钉客户端工厂，供测试注入假实现；默认 :class:`DingTalkClient`。
    """

    def __init__(
        self,
        session_factory: SessionFactory | None,
        *,
        client_factory: Callable[..., DingTalkClient] = DingTalkClient,
    ) -> None:
        self._session_factory = session_factory
        self._client_factory = client_factory

    async def send_review_completed(
        self,
        gitlab_project_id: int,
        review_data: dict[str, Any],
    ) -> None:
        """推送 Review 完成通知到项目配置的所有启用渠道。

        Args:
            gitlab_project_id: GitLab 数值项目 ID；服务内部解析为 DB Project UUID 后查渠道。
            review_data: 评审结果摘要，字段约定见 :meth:`_build_review_message`。

        渠道查询失败 / 推送失败均 fail-silent，仅记日志，不抛异常。
        """

        channels = await self._resolve_channels(gitlab_project_id)
        if not channels:
            return
        title, text = self._build_review_message(review_data)
        for channel in channels:
            await self._dispatch(channel, title, text)

    async def _dispatch(
        self,
        channel: ProjectNotificationChannel,
        title: str,
        text: str,
    ) -> None:
        """单渠道推送；任何异常吞掉，不影响其它渠道与主流程。

        ``channel.webhook_url`` / ``channel.secret`` 由 :class:`EncryptedString`
        读取时自动解密；解密失败会在访问属性时抛异常，被这里的 ``except`` 兜住。
        """

        if channel.channel_type != "dingtalk":
            logger.debug(
                "skip unsupported notification channel",
                extra={"channel_type": channel.channel_type, "channel_id": str(channel.id)},
            )
            return
        try:
            client = self._client_factory(
                webhook_url=channel.webhook_url,
                secret=channel.secret,
            )
            await client.send_markdown(title, text)
        except Exception:
            logger.warning(
                "failed to push review notification; continuing",
                exc_info=True,
                extra={
                    "channel_id": str(channel.id),
                    "channel_type": channel.channel_type,
                },
            )

    async def _resolve_channels(
        self,
        gitlab_project_id: int,
    ) -> list[ProjectNotificationChannel]:
        """解析项目下启用的通知渠道。

        - ``session_factory`` 未注入：返回空（MVP 兼容）。
        - Project 未在管理后台注册：返回空。
        - DB / 解密异常：返回空，不抛（fail-silent）。
        """

        if self._session_factory is None:
            return []
        try:
            async with self._session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(
                    str(gitlab_project_id),
                )
                if project is None:
                    return []
                channel_repo = ProjectNotificationChannelRepository(session)
                return await channel_repo.get_enabled_by_project(project.id)
        except Exception:
            logger.warning(
                "failed to resolve notification channels; skipping push",
                exc_info=True,
                extra={"gitlab_project_id": gitlab_project_id},
            )
            return []

    def _build_review_message(self, review_data: dict[str, Any]) -> tuple[str, str]:
        """构造消息标题与 markdown 正文。

        ``review_data`` 约定字段：``review_id`` / ``mr_iid`` / ``mr_title`` /
        ``finding_count`` / ``has_blocker`` / ``blocker_count`` / ``detail_url`` /
        ``status``（``"done"`` / ``"engine_error"``）。

        Returns:
            ``(title, text)``：标题用于钉钉通知列表展示，text 为 markdown 正文。
        """

        status_value = str(review_data.get("status") or "done")
        mr_iid = review_data.get("mr_iid")
        mr_title = str(review_data.get("mr_title") or "")
        finding_count = int(review_data.get("finding_count") or 0)
        blocker_count = int(review_data.get("blocker_count") or 0)
        has_blocker = bool(review_data.get("has_blocker"))
        review_id = review_data.get("review_id")
        detail_url = review_data.get("detail_url")

        mr_label = f"!{mr_iid}" if mr_iid is not None else "未知"
        if status_value == "engine_error":
            title = f"【AI Code Review】MR {mr_label} 审查异常"
            result_line = "引擎执行失败，未产出审查结果"
        elif has_blocker:
            title = f"【AI Code Review】MR {mr_label} 审查完成 - 存在阻断"
            result_line = f"发现问题 {finding_count} 个（其中 {blocker_count} 个阻断）"
        else:
            title = f"【AI Code Review】MR {mr_label} 审查完成 - 无阻断"
            result_line = f"发现问题 {finding_count} 个（无阻断）"

        lines = [f"### {title}", ""]
        if mr_title:
            lines.append(f"**MR 标题**：{mr_title}")
        lines.append(f"**Review ID**：{review_id}")
        lines.append(f"**结果**：{result_line}")
        if detail_url:
            lines.append(f"**详情**：[查看详情]({detail_url})")
        return title, "\n".join(lines)
