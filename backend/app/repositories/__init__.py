"""Repository 层：把数据访问逻辑从业务代码中抽离。

设计目标
--------
- 业务代码（API 路由 / orchestrator）只依赖 Repository 接口，不直接调用 SQLAlchemy
  的 ``AsyncSession.execute / scalar / add / commit / delete``。
- 便于测试替换（未来可注入 in-memory / mock repository）。
- 便于跨方言（PostgreSQL / MySQL）——查询语法收敛在少数几个仓储类里。

约定
----
- ``BaseRepository`` 提供通用 CRUD（get / list / add / delete / count）。
- 每个具体 Repository 只包含**特定于该模型的查询**（例如按名称查、按外键过滤）。
- 事务边界仍由上层控制：Repository 内部只做 ``flush()``，最终 ``commit()`` 由
  路由层的 ``_commit_or_400`` helper 决定。
"""

from app.repositories.audit_log import AuditLogRepository
from app.repositories.base import BaseRepository
from app.repositories.engine import EngineRepository
from app.repositories.negative_example import NegativeExampleRepository
from app.repositories.project import ProjectRepository
from app.repositories.project_block_policy import ProjectBlockPolicyRepository
from app.repositories.project_rule import ProjectRuleRepository
from app.repositories.provider import ProviderRepository
from app.repositories.review import FindingRepository, ReviewRepository
from app.repositories.rule import RuleRepository

__all__ = [
    "AuditLogRepository",
    "BaseRepository",
    "EngineRepository",
    "FindingRepository",
    "NegativeExampleRepository",
    "ProjectBlockPolicyRepository",
    "ProjectRepository",
    "ProjectRuleRepository",
    "ProviderRepository",
    "ReviewRepository",
    "RuleRepository",
]
