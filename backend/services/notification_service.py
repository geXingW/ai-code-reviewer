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
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from core.finding_taxonomy import FindingCategory, category_display
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

# 北京时间时区（UTC+8），用于钉钉通知消息里 MR / commit 创建时间的展示。
# 存储层保持 UTC 不变，只在面向用户的展示环节做转换。
_CN_TZ = timezone(timedelta(hours=8))
# GitLab webhook 时间字符串尾部常见的时区后缀：``UTC`` / ``+08:00`` / ``+0800``。
_TZ_SUFFIX_RE = re.compile(r"(?P<tz>[A-Za-z]{1,5}|[+-]\d{2}:?\d{2})\s*$")

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


def _parse_created_at(raw: str) -> datetime | None:
    """把 GitLab webhook 常见时间格式解析为 aware datetime（UTC 语义）。

    GitLab webhook 的 ``created_at`` / commit ``timestamp`` 常见格式：

    - ``2026-08-23 10:31:01 UTC``（Ruby ``to_s`` 风格，国内用户最常踩的坑）
    - ``2026-08-20T03:30:16.000Z`` / ``2026-08-20T03:30:16Z``（带 Z 后缀）
    - ``2026-08-20T03:30:16+00:00`` / ``2026-08-20T11:30:16+08:00``
    - ``2026-08-20T03:30:16+0800``（无冒号偏移）

    无时区信息的裸时间按 UTC 处理（GitLab 服务器统一以 UTC 存储）；解析失败
    返回 ``None``，由调用方原样回退，避免因时间格式异常阻断整条通知。
    """

    text = (raw or "").strip()
    if not text:
        return None
    tz_suffix: str | None = None
    match = _TZ_SUFFIX_RE.search(text)
    if match:
        tz_suffix = match.group("tz")
        text = text[: match.start()].strip()
    # Python 3.10 的 fromisoformat 只认 "T" 分隔，先统一再解析。
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        if tz_suffix and tz_suffix[0] in "+-":
            sign = 1 if tz_suffix[0] == "+" else -1
            digits = tz_suffix[1:].replace(":", "")
            offset = timedelta(
                hours=int(digits[:2]),
                minutes=int(digits[2:4] or 0),
            )
            dt = dt.replace(tzinfo=timezone(sign * offset))
        else:
            # "UTC" / "GMT" 等缩写，以及完全无时区的裸时间：GitLab 按 UTC 存储。
            dt = dt.replace(tzinfo=UTC)
    return dt


def _format_created_at(raw: str) -> str:
    """把 GitLab 返回的时间字符串转换为北京时间 ``YYYY-MM-DD HH:MM:SS``。

    解析失败时原样返回，避免因时间格式异常阻断整条通知发送。
    """

    dt = _parse_created_at(raw)
    if dt is None:
        return raw
    return dt.astimezone(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _kv(label: str, value: str | None) -> str | None:
    """渲染 ``- 标签: 值`` 字段行；值为空（缺失 / 纯空白）时返回 ``None``。

    「提交信息 / 审查摘要」区块的字段都是"有值才渲染"的逐行降级模式，
    统一在这里收口：调用方声明字段清单后 ``filter`` 掉 ``None`` 即可，
    替代成串的 ``if value: lines.append(...)``。
    """

    text = str(value or "").strip()
    return f"- {label}: {text}" if text else None


def _link(label: str, url: str | None) -> str | None:
    """渲染 ``- [文案](链接)`` 行；链接为空时返回 ``None``。"""

    text = str(url or "").strip()
    return f"- [{label}]({text})" if text else None


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

    @staticmethod
    def _collect_authors(review_data: dict[str, Any]) -> list[str]:
        """收集本次审查需要真实 @ 的提交人（去重、保序），供 atMobiles 解析使用。

        顺序：commits 各 commit 作者（旧 -> 新）+ push 触发者 / MR 创建人。
        commit 作者取 ``commit.author_name``（push webhook 的 commit 对象只有
        name/email 没有 username，尽力按 gitlab_username 查映射，查不到跳过）；
        触发者优先 ``mr_author_username``（与 user_mappings 查询键一致），
        缺失时回退 ``mr_author_name``。空字符串全部跳过；返回空列表表示无
        提交人可 @。
        """

        authors: list[str] = []
        commits = review_data.get("commits")
        if isinstance(commits, list):
            for commit in commits:
                if not isinstance(commit, dict):
                    continue
                name = str(commit.get("author_name") or "").strip()
                if name and name not in authors:
                    authors.append(name)
        trigger = (
            str(review_data.get("mr_author_username") or "").strip()
            or str(review_data.get("mr_author_name") or "").strip()
        )
        if trigger and trigger not in authors:
            authors.append(trigger)
        return authors

    async def _resolve_at_mobiles(
        self,
        gitlab_project_id: int,
        review_data: dict[str, Any],
    ) -> list[str]:
        """解析要 @ 的手机号列表（本次审查**所有提交人**的钉钉绑定手机号）。

        - ``review_data`` 中无提交人 / ``session_factory`` 未注入：返回空列表
          （MVP 兼容、无作者信息时不 @ 人）。
        - 提交人集合由 :meth:`_collect_authors` 给出：push 触发者 / MR 创建人
          （``mr_author_username``，与 user_mappings 查询键一致）+ 多 commit
          push 各 commit 作者（``commits[].author_name``，尽力按
          gitlab_username 匹配）；查询键重复自动去重，手机号同样去重。
        - Project 未注册：返回空。
        - 映射表查不到某个 GitLab 用户名：**fail-silent**，记 debug 日志跳过
          该用户，绝不因「没配置映射」阻断通知；其余用户继续解析。
        - DB 异常：记 warning 返回空，不影响推送。
        """

        usernames = self._collect_authors(review_data)
        if not usernames or self._session_factory is None:
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
                mobiles: list[str] = []
                for username in usernames:
                    mapping = await mapping_repo.get_by_gitlab_username(
                        project.id,
                        username,
                    )
                    if mapping is None:
                        logger.debug(
                            "no user mapping for review author; skipping @ mention",
                            extra={
                                "gitlab_project_id": gitlab_project_id,
                                "gitlab_username": username,
                            },
                        )
                        continue
                    if mapping.dingtalk_mobile not in mobiles:
                        mobiles.append(mapping.dingtalk_mobile)
                return mobiles
        except Exception:
            logger.warning(
                "failed to resolve at-mobiles for review authors; pushing without @",
                exc_info=True,
                extra={
                    "gitlab_project_id": gitlab_project_id,
                    "gitlab_usernames": usernames,
                },
            )
            return []

    def _build_review_message(self, review_data: dict[str, Any]) -> tuple[str, str]:
        """构造消息标题与 markdown 正文。

        正文分两层：``提交信息`` 区块（MR / commit 维度信息与审查详情页链接，
        任一字段缺失时逐行降级，全缺失时整个区块跳过）+ ``AI Review 结果``
        区块（审查摘要、按严重级别分组的问题列表），区块与每条问题之间用空行
        分隔，便于钉钉端阅读。

        ``review_data`` 约定字段：``review_id`` / ``mr_iid`` / ``mr_title`` /
        ``finding_count`` / ``has_blocker`` / ``blocker_count`` / ``detail_url`` /
        ``status``（``"done"`` / ``"engine_error"``）/ ``review_kind``（``"mr"``
        默认 / ``"commit"``，决定标题与标签前缀），以及可选字段：

        - ``gitlab_web_url: str | None``：GitLab 页面链接（MR 审查为 MR 链接、
          commit push 审查为 head commit 链接），「提交信息」区块用。
        - ``mr_author_username`` / ``mr_author_name``：MR 创建人信息
          （@ 人由 :meth:`_resolve_at_mobiles` 处理，这里用于正文显示）。
        - ``mr_created_at: str``：MR / commit 创建时间（ISO 或 Ruby ``to_s``
          字符串），可能为空；展示时统一转北京时间。
        - ``commits: list[dict] | None``：多 commit push 审查的完整 commit
          列表，形如 ``[{"id", "title", "message", "timestamp", "url",
          "author_name"}, ...]``（时间序，旧 -> 新）。非空时「提交信息」区块
          逐条列出每个 commit（短 SHA + 标题 / 提交者 @作者名 / 时间 / 提交
          详情链接），参照 AI-Codereview-Gitlab 的多提交展示方式；为
          ``None``（MR / 单 commit 审查）时回退单条展示。
        - ``findings_summary: list[dict] | None``：按严重级别分组的精简 finding
          列表，形如 ``[{"severity": "BLOCKER", "items": [{"title",
          "file_path", "line_number", "severity", "category"}, ...]}, ...]``。
          BLOCKER 全部展示，WARNING / INFO 各最多 5 条，超出显示
          「还有 N 条，详见详情页」；每条独立成段展示问题类型 / 严重程度 /
          相关文件 / 代码位置。
        - ``changed_files_count: int``：变更文件数；为 0（缺失 / 非 incremental
          模式）时跳过「变更规模」行。

        Returns:
            ``(title, text)``：标题用于钉钉通知列表展示，text 为 markdown 正文。
        """

        status_value = str(review_data.get("status") or "done")
        mr_iid = review_data.get("mr_iid")
        mr_title = str(review_data.get("mr_title") or "")
        gitlab_web_url = review_data.get("gitlab_web_url")
        finding_count = int(review_data.get("finding_count") or 0)
        blocker_count = int(review_data.get("blocker_count") or 0)
        has_blocker = bool(review_data.get("has_blocker"))
        detail_url = review_data.get("detail_url")
        findings_summary = review_data.get("findings_summary")
        changed_files_count = int(review_data.get("changed_files_count") or 0)
        mr_author_name = str(review_data.get("mr_author_name") or "")
        mr_author_username = str(review_data.get("mr_author_username") or "")
        mr_created_at = str(review_data.get("mr_created_at") or "")
        # 多 commit push 审查的完整 commit 列表（含各 commit 作者名）；
        # 非空时「提交信息」区块逐条列出并 @ 各作者，None 回退单条展示。
        commits = review_data.get("commits")
        # 审查对象类型：默认 MR；commit push 审查传 "commit"，标题用 Commit 短 SHA。
        review_kind = str(review_data.get("review_kind") or "mr")

        title = self._build_title(
            review_kind=review_kind,
            mr_iid=mr_iid,
            status_value=status_value,
            has_blocker=has_blocker,
        )

        lines = [f"### {title}", ""]

        mr_section = self._build_mr_section(
            mr_title=mr_title,
            # @ 优先 GitLab 用户名（与 user_mappings 的查询键一致）。
            author=mr_author_username or mr_author_name,
            created_at=mr_created_at,
            gitlab_web_url=gitlab_web_url,
            detail_url=detail_url,
            review_kind=review_kind,
            commits=commits,
        )
        if mr_section:
            lines.extend(mr_section)
            lines.append("")

        lines.append("**🤖 AI Review 结果**")
        if status_value == "engine_error":
            lines.extend(["", "引擎执行失败，未产出审查结果"])
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
                lines.extend(["", "**📋 关键问题清单**"])
                lines.extend(self._build_findings_section(findings_summary))

        text = "\n".join(lines)
        if len(text) > _MAX_MESSAGE_LENGTH:
            # 超长会被钉钉整条拒绝；截断保底，细节引导到详情页。
            # 截断保留头部：标题与「提交信息」区块（含 @创建人）始终完整；
            # 真实 @手机号由钉钉客户端在截断后的正文末尾追加，同样不会被截掉。
            text = (
                text[:_MAX_MESSAGE_LENGTH]
                + "\n\n...（消息过长已截断，详见详情页）"
            )
        return title, text

    @staticmethod
    def _build_title(
        *,
        review_kind: str,
        mr_iid: object,
        status_value: str,
        has_blocker: bool,
    ) -> str:
        """构造通知标题：``【AI Code Review】<Commit|MR> <标识> <结论>``。

        commit push 审查（``review_kind="commit"``）用 Commit 短 SHA 作标识，
        MR 审查用 ``MR <iid>``；标识缺失时降级「未知」。结论三选一：审查异常 /
        存在阻断 / 无阻断。
        """

        label_prefix = "Commit" if review_kind == "commit" else "MR"
        mr_label = f"{label_prefix} {mr_iid}" if mr_iid is not None else "未知"
        if status_value == "engine_error":
            return f"【AI Code Review】{mr_label} 审查异常"
        if has_blocker:
            return f"【AI Code Review】{mr_label} 审查完成 - 存在阻断"
        return f"【AI Code Review】{mr_label} 审查完成 - 无阻断"

    @staticmethod
    def _build_mr_section(
        *,
        mr_title: str,
        author: str,
        created_at: str,
        gitlab_web_url: str | None,
        detail_url: str | None,
        review_kind: str = "mr",
        commits: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        """构造「提交信息」区块；六个字段全为空时返回空列表（跳过整个区块）。

        ``created_at`` 来自 GitLab webhook（ISO 或 Ruby ``to_s`` 字符串，常带
        ``UTC`` 后缀 / 时区偏移），展示时统一转换为北京时间（UTC+8）并去掉
        时区后缀，避免国内用户看到 ``10:31:01 UTC`` 这类反直觉的时间。
        ``author`` 用 GitLab 用户名（与 user_mappings 查询键一致），位于区块
        头部，超长截断（保留头部）不会被截掉。
        ``detail_url`` 为审查详情页链接，渲染在区块末尾，供快速跳转完整结果。
        ``gitlab_web_url`` 按 ``review_kind`` 区分链接文案（MR / 提交详情）。
        ``commits`` 非空（多 commit push 审查）时跳过单条字段展示，改为逐条
        列出每个 commit（参照 AI-Codereview-Gitlab：短 SHA + 标题 / 提交者
        @作者名 / 时间 / 提交详情链接），每个 commit 的作者以 @ 形式展示，
        不依赖 user_mappings 映射配置。
        """

        if commits:
            return NotificationService._build_commits_section(commits)
        if not (mr_title or author or created_at or gitlab_web_url or detail_url):
            return []
        link_label = "查看提交详情" if review_kind == "commit" else "查看MR详情"
        # detail_url 渲染已下线（v0.0.2），仅保留参数参与区块的空值判断。
        field_lines = [
            _kv("标题", mr_title),
            _kv("创建人", author),
            _kv("创建时间", _format_created_at(created_at) if created_at else None),
            _link(link_label, gitlab_web_url),
        ]
        return ["**📋 提交信息**", "", *(line for line in field_lines if line)]

    @staticmethod
    def _build_commits_section(commits: list[dict[str, Any]]) -> list[str]:
        """多 commit push 审查的「提交信息」区块：逐条列出每个 commit。

        参照 AI-Codereview-Gitlab 的展示方式（每条 commit 一组：提交信息 /
        提交者 / 时间 / 提交详情链接），适配本项目的 markdown 风格：每条
        commit 以 ``**N. 短SHA 标题**`` 开头，组内字段化展示，组间空行分隔；
        提交者渲染为 ``@作者名``（来自 webhook ``commit.author.name``），即使
        未配置 user_mappings 也展示，且位于正文头部，超长截断（保留头部）
        不会被截掉；真实 @高亮由 atMobiles（所有提交人中配置了映射的）负责。
        """

        lines = ["**📋 提交信息**", ""]
        for index, commit in enumerate(commits, start=1):
            lines.extend(NotificationService._render_commit_entry(index, commit))
        return lines

    @staticmethod
    def _render_commit_entry(index: int, commit: dict[str, Any]) -> list[str]:
        """渲染单条 commit 展示组：加粗标题行 + 字段行 + 组尾空行。"""

        commit_sha = str(commit.get("id") or "")[:8]
        title = str(commit.get("title") or "").strip()
        author_name = str(commit.get("author_name") or "")
        created_at = str(commit.get("timestamp") or "")
        url = str(commit.get("url") or "")

        header = f"**{index}. {commit_sha}"
        if title:
            header += f" {title}"
        field_lines = [
            _kv("提交者", f"@{author_name}" if author_name else None),
            _kv("时间", _format_created_at(created_at) if created_at else None),
            _link("查看提交详情", url),
        ]
        return [header + "**", *(line for line in field_lines if line), ""]

    @staticmethod
    def _build_summary_section(
        *,
        changed_files_count: int,
        findings_summary: list[dict[str, Any]] | None,
        finding_count: int,
        blocker_count: int,
    ) -> list[str]:
        """构造「📋 审查摘要」区块；各字段缺失时逐行降级跳过。"""

        field_lines = [
            _kv("变更规模", f"涉及 {changed_files_count} 个文件")
            if changed_files_count > 0
            else None,
            _kv(
                "总体评价",
                NotificationService._build_result_line(
                    findings_summary,
                    finding_count,
                    blocker_count,
                ),
            ),
        ]
        return ["**📋 审查摘要**", "", *(line for line in field_lines if line)]

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

        参照 AI-Codereview-Gitlab 的展示风格：每条问题独立成段（标题 + 问题
        类型 / 严重程度 / 相关文件 / 代码位置），段落间空行分隔，避免长列表
        挤成一团；空分组（0 条问题）跳过不渲染。
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
            lines.extend(["", f"**{badge} {label}问题 ({len(items)})**", ""])
            for index, item in enumerate(shown, start=1):
                lines.extend(NotificationService._render_finding_item(item, index))
            omitted = len(items) - len(shown)
            if omitted > 0:
                lines.append(f"...（还有 {omitted} 条，详见详情页）")
        return lines

    @staticmethod
    def _render_finding_item(item: dict[str, Any], index: int) -> list[str]:
        """渲染单条问题的展示段落：加粗标题 + 字段行 + 段尾空行。"""

        title_text = str(item.get("title") or "")
        file_path = str(item.get("file_path") or "")
        line_number = item.get("line_number")
        location = f"{file_path}:{line_number}" if line_number else file_path
        category = NotificationService._format_category(item.get("category"))
        severity = NotificationService._format_severity(item.get("severity"))
        return [
            f"**{index}. {title_text}**",
            "",
            *filter(
                None,
                [
                    _kv("问题类型", category),
                    _kv("严重程度", severity),
                    _kv("相关文件", f"`{file_path}`"),
                    _kv("代码位置", f"`{location}`"),
                ],
            ),
            "",
        ]

    @staticmethod
    def _format_category(category_raw: object) -> str:
        """finding 分类 → 「emoji 中文标签」；缺失 / 非法值兜底「其他」。"""

        if not category_raw:
            return "📝 其他"
        try:
            emoji, label = category_display(FindingCategory(str(category_raw)))
        except ValueError:
            return "📝 其他"
        return f"{emoji} {label}"

    @staticmethod
    def _format_severity(severity_raw: object) -> str:
        """严重级别 → 「emoji 中文标签」；未知值兜底中性圆点。"""

        severity = str(severity_raw or "").upper()
        if severity in _SEVERITY_META:
            badge, label = _SEVERITY_META[severity]
            return f"{badge} {label}"
        return "⚪ 未知"
