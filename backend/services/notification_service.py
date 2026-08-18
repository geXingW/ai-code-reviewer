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

from integrations.dingtalk.client import DingTalkClient
from models.project_notification_channel import ProjectNotificationChannel
from repositories.project import ProjectRepository
from repositories.project_notification_channel import (
    ProjectNotificationChannelRepository,
)
from repositories.user_mapping_repository import UserMappingRepository

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    # 与 ReviewOrchestrator.SessionFactory 同语义：返回 AsyncSession 上下文管理器的零参可调用。
    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

logger = logging.getLogger(__name__)

# 严重级别 -> (emoji 徽章, 中文标签)。通知正文分组与结果行共用。
_SEVERITY_META: dict[str, tuple[str, str]] = {
    "BLOCKER": ("🔴", "阻断"),
    "WARNING": ("🟡", "警告"),
    "INFO": ("🔵", "提示"),
}
# 每个级别在正文中最多展示的条数；None 表示全部展示（BLOCKER 通常数量少）。
_MAX_ITEMS_PER_SEVERITY: dict[str, int | None] = {
    "BLOCKER": None,
    "WARNING": 5,
    "INFO": 5,
}
# 钉钉 markdown 正文上限约 20000 字，超长会被整条拒绝；预留安全余量，
# 超出部分截断并提示到详情页。
_MAX_MESSAGE_LENGTH = 12_000


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
        at_mobiles = await self._resolve_at_mobiles(gitlab_project_id, review_data)
        for channel in channels:
            await self._dispatch(channel, title, text, at_mobiles)

    async def _dispatch(
        self,
        channel: ProjectNotificationChannel,
        title: str,
        text: str,
        at_mobiles: list[str] | None = None,
    ) -> None:
        """单渠道推送；任何异常吞掉，不影响其它渠道与主流程。

        ``at_mobiles`` 为 MR 创建人的钉钉手机号（来自 user_mappings 映射表），
        传给钉钉客户端实现 @ 人；为空时不 @。

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
            await client.send_markdown(title, text, at_mobiles=at_mobiles or None)
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

    async def _resolve_at_mobiles(
        self,
        gitlab_project_id: int,
        review_data: dict[str, Any],
    ) -> list[str]:
        """解析要 @ 的手机号列表（MR 创建人的钉钉绑定手机号）。

        - ``review_data["mr_author_username"]`` 缺失 / ``session_factory`` 未注入：
          返回空列表（MVP 兼容、MR 无作者信息时不 @ 人）。
        - Project 未注册：返回空。
        - 映射表查不到该 GitLab 用户名：**fail-silent**，记 debug 日志返回空，
          绝不因「没配置映射」阻断通知。
        - DB 异常：记 warning 返回空，不影响推送。
        """

        author_username = review_data.get("mr_author_username")
        if not author_username or self._session_factory is None:
            return []
        try:
            async with self._session_factory() as session:
                project_repo = ProjectRepository(session)
                project = await project_repo.get_by_gitlab_project_id(
                    str(gitlab_project_id),
                )
                if project is None:
                    return []
                mapping_repo = UserMappingRepository(session)
                mapping = await mapping_repo.get_by_gitlab_username(
                    project.id,
                    str(author_username),
                )
                if mapping is None:
                    logger.debug(
                        "no user mapping for mr author; skipping @ mention",
                        extra={
                            "gitlab_project_id": gitlab_project_id,
                            "gitlab_username": author_username,
                        },
                    )
                    return []
                return [mapping.dingtalk_mobile]
        except Exception:
            logger.warning(
                "failed to resolve at-mobiles for mr author; pushing without @",
                exc_info=True,
                extra={
                    "gitlab_project_id": gitlab_project_id,
                    "gitlab_username": author_username,
                },
            )
            return []

    def _build_review_message(self, review_data: dict[str, Any]) -> tuple[str, str]:
        """构造消息标题与 markdown 正文。

        正文分两层：``MR信息`` 区块（MR 维度信息，任一字段缺失时
        逐行降级，全缺失时整个区块跳过）+ ``AI Review 结果`` 区块（审查摘要、
        按严重级别分组的问题列表、详情页链接）。

        ``review_data`` 约定字段：``review_id`` / ``mr_iid`` / ``mr_title`` /
        ``finding_count`` / ``has_blocker`` / ``blocker_count`` / ``detail_url`` /
        ``status``（``"done"`` / ``"engine_error"``），以及可选字段：

        - ``mr_web_url: str | None``：MR 跳转链接，「MR信息」区块用。
        - ``mr_author_username`` / ``mr_author_name``：MR 创建人信息
          （@ 人由 :meth:`_resolve_at_mobiles` 处理，这里用于显示创建人）。
        - ``mr_created_at: str``：MR 创建时间。
        - ``findings_summary: list[dict] | None``：按严重级别分组的精简 finding
          列表，形如 ``[{"severity": "BLOCKER", "items": [{"title", "file_path",
          "line_number"}, ...]}, ...]``。BLOCKER 全部展示，WARNING / INFO 各
          最多 5 条，超出显示「还有 N 条，详见详情页」。
        - ``mr_created_at: str``：MR 创建时间（ISO 字符串），可能为空。
        - ``changed_files_count: int``：变更文件数；为 0（缺失 / 非 incremental
          模式）时跳过「变更规模」行。

        Returns:
            ``(title, text)``：标题用于钉钉通知列表展示，text 为 markdown 正文。
        """

        status_value = str(review_data.get("status") or "done")
        mr_iid = review_data.get("mr_iid")
        mr_title = str(review_data.get("mr_title") or "")
        mr_web_url = review_data.get("mr_web_url")
        finding_count = int(review_data.get("finding_count") or 0)
        blocker_count = int(review_data.get("blocker_count") or 0)
        has_blocker = bool(review_data.get("has_blocker"))
        detail_url = review_data.get("detail_url")
        findings_summary = review_data.get("findings_summary")
        changed_files_count = int(review_data.get("changed_files_count") or 0)
        mr_author_name = str(review_data.get("mr_author_name") or "")
        mr_author_username = str(review_data.get("mr_author_username") or "")
        mr_created_at = str(review_data.get("mr_created_at") or "")

        mr_label = f"!{mr_iid}" if mr_iid is not None else "未知"
        if status_value == "engine_error":
            title = f"【AI Code Review】MR {mr_label} 审查异常"
        elif has_blocker:
            title = f"【AI Code Review】MR {mr_label} 审查完成 - 存在阻断"
        else:
            title = f"【AI Code Review】MR {mr_label} 审查完成 - 无阻断"

        lines = [f"### {title}", ""]

        mr_section = self._build_mr_section(
            mr_title=mr_title,
            author=mr_author_name or mr_author_username,
            created_at=mr_created_at,
            web_url=mr_web_url,
        )
        if mr_section:
            lines.extend(mr_section)
            lines.append("")

        lines.append("AI Review 结果:")
        if status_value == "engine_error":
            lines.append("引擎执行失败，未产出审查结果")
        else:
            lines.append("")
            lines.extend(
                self._build_summary_section(
                    changed_files_count=changed_files_count,
                    findings_summary=findings_summary,
                    finding_count=finding_count,
                    blocker_count=blocker_count,
                ),
            )
            if findings_summary:
                lines.extend(self._build_findings_section(findings_summary))

        if detail_url:
            lines.append("")
            lines.append(f"[查看完整审查详情]({detail_url})")

        text = "\n".join(lines)
        if len(text) > _MAX_MESSAGE_LENGTH:
            # 超长会被钉钉整条拒绝；截断保底，细节引导到详情页。
            text = (
                text[:_MAX_MESSAGE_LENGTH]
                + "\n\n...（消息过长已截断，详见详情页）"
            )
        return title, text

    @staticmethod
    def _build_mr_section(
        *,
        mr_title: str,
        author: str,
        created_at: str,
        web_url: str | None,
    ) -> list[str]:
        """构造「MR信息」区块；四个字段全为空时返回空列表（跳过整个区块）。"""

        if not (mr_title or author or created_at or web_url):
            return []
        lines = ["MR信息:"]
        if mr_title:
            lines.append(f"- MR标题: {mr_title}")
        if author:
            lines.append(f"- 创建人: {author}")
        if created_at:
            lines.append(f"- 创建时间: {created_at}")
        if web_url:
            lines.append(f"- [查看MR详情]({web_url})")
        return lines

    @staticmethod
    def _build_summary_section(
        *,
        changed_files_count: int,
        findings_summary: list[dict[str, Any]] | None,
        finding_count: int,
        blocker_count: int,
    ) -> list[str]:
        """构造「📋 审查摘要」区块；各字段缺失时逐行降级跳过。"""

        lines = ["📋 审查摘要"]
        if changed_files_count > 0:
            lines.append(f"- 变更规模：涉及 {changed_files_count} 个文件")
        result_line = NotificationService._build_result_line(
            findings_summary,
            finding_count,
            blocker_count,
        )
        lines.append(f"- 总体评价：{result_line}")
        return lines

    @staticmethod
    def _build_result_line(
        findings_summary: list[dict[str, Any]] | None,
        finding_count: int,
        blocker_count: int,
    ) -> str:
        """构造「总体评价」行；有分组摘要时按级别计数，否则退回旧的总量文案。"""

        if not findings_summary:
            if blocker_count:
                return f"发现问题 {finding_count} 个（其中 {blocker_count} 个阻断）"
            return f"发现问题 {finding_count} 个（无阻断）"
        counts = {severity: 0 for severity in _SEVERITY_META}
        for group in findings_summary:
            severity = str(group.get("severity") or "")
            if severity in counts:
                counts[severity] = len(group.get("items") or [])
        return " · ".join(
            f"{badge} {label} {counts[severity]} 个"
            for severity, (badge, label) in _SEVERITY_META.items()
        )

    @staticmethod
    def _build_findings_section(findings_summary: list[dict[str, Any]]) -> list[str]:
        """按严重级别渲染分组 finding 列表（BLOCKER 全展示，其余各最多 5 条）。

        空分组（0 条问题）跳过不渲染，避免正文出现无意义的「0 个问题」标题。
        """

        lines: list[str] = []
        for group in findings_summary:
            severity = str(group.get("severity") or "")
            if severity not in _SEVERITY_META:
                continue
            badge, label = _SEVERITY_META[severity]
            items = list(group.get("items") or [])
            if not items:
                continue
            max_items = _MAX_ITEMS_PER_SEVERITY[severity]
            shown = items if max_items is None else items[:max_items]
            lines.append("")
            lines.append(f"**{badge} {label}问题 ({len(items)})**")
            lines.append("")
            for index, item in enumerate(shown, start=1):
                title_text = str(item.get("title") or "")
                file_path = str(item.get("file_path") or "")
                line_number = item.get("line_number")
                location = f"{file_path}:{line_number}" if line_number else file_path
                lines.append(f"{index}. **{title_text}** - `{location}`")
            omitted = len(items) - len(shown)
            if omitted > 0:
                lines.append(f"...（还有 {omitted} 条，详见详情页）")
        return lines
