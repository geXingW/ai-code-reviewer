"""Shared block-policy resolution with skip semantics for all review entries."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from core.block_policy import (
    BlockPolicyLike,
    build_default_block_policies,
    match_block_policy,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def resolve_policy_or_skip(
    *,
    block_policies: Sequence[BlockPolicyLike] | None,
    project_uuid: UUID,
    project_id: int,
    branch: str,
) -> tuple[BlockPolicyLike, str] | None:
    """按分支匹配 block policy，未命中时跳过审核。

    项目配置了 block_policies 时按配置匹配，否则回退默认模板（只覆盖
    主干 / 发布分支）。MR / commit / push 三个审核入口共用，保证过滤
    行为一致：返回 ``(block_policy, policy_applied)`` 表示继续审核；
    返回 ``None`` 表示未匹配到策略，调用方直接返回 ``skipped_no_policy``
    （只记日志，不评论、不设 status、不通知）。
    """

    block_policy = match_block_policy(
        block_policies or build_default_block_policies(project_uuid),
        branch,
    )
    if block_policy is None:
        logger.info(
            "branch not matched by any block policy; skip review",
            extra={
                "gitlab_project_id": project_id,
                "branch": branch,
            },
        )
        return None
    policy_applied = f"{block_policy.branch_pattern} -> {block_policy.block_severity}"
    logger.info("Applying policy", extra={"policy_applied": policy_applied})
    return block_policy, policy_applied
