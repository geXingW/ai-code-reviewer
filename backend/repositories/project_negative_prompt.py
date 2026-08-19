"""项目级负样本提示词仓储。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.negative_example import NegativeExample
from models.project_negative_prompt import ProjectNegativePrompt


class ProjectNegativePromptRepository:
    """``project_negative_prompts`` 表数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_content(self, project_id: UUID) -> str:
        """读取项目提示词内容，无记录时返回空字符串。"""

        result = await self._session.execute(
            select(ProjectNegativePrompt).where(
                ProjectNegativePrompt.project_id == project_id,
            ),
        )
        prompt = result.scalar_one_or_none()
        return prompt.content if prompt is not None else ""

    async def upsert(self, project_id: UUID, content: str) -> None:
        """有记录则 UPDATE，无记录则 INSERT（upsert 模式）。"""

        result = await self._session.execute(
            select(ProjectNegativePrompt).where(
                ProjectNegativePrompt.project_id == project_id,
            ),
        )
        prompt = result.scalar_one_or_none()
        if prompt is not None:
            prompt.content = content
        else:
            self._session.add(ProjectNegativePrompt(project_id=project_id, content=content))
        await self._session.flush()

    async def count_approved_examples(self, project_id: UUID) -> int:
        """统计该项目已批准（``approved_at IS NOT NULL``）的负样本数量。"""

        stmt = (
            select(func.count())
            .select_from(NegativeExample)
            .where(
                NegativeExample.project_id == project_id,
                NegativeExample.approved_at.is_not(None),
            )
        )
        count = await self._session.scalar(stmt)
        return int(count or 0)
