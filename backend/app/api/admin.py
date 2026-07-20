"""MVP admin REST API endpoints for CRUD and false-positive workflows."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, TypeVar, cast
from uuid import UUID, uuid4

import jwt as pyjwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, asc, desc, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.db import Base, DbSession
from app.models.engine import Engine
from app.models.finding import Finding
from app.models.negative_example import NegativeExample
from app.models.project import Project
from app.models.project_block_policy import ProjectBlockPolicy
from app.models.project_rule import ProjectRule
from app.models.provider import Provider
from app.models.review import Review
from app.models.rule import Rule
from app.repositories import BaseRepository, ProjectRepository, RuleRepository
from app.schemas.engine import EngineCreate, EngineRead, EngineUpdate
from app.schemas.finding import FindingCreate, FindingRead, FindingUpdate
from app.schemas.negative_example import NegativeExampleRead
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from app.schemas.project_block_policy import ProjectBlockPolicyCreate
from app.schemas.project_rule import ProjectRuleCreate
from app.schemas.provider import ProviderCreate, ProviderRead, ProviderUpdate
from app.schemas.review import ReviewCreate, ReviewRead, ReviewUpdate
from app.schemas.rule import RuleCreate, RuleRead, RuleUpdate

ModelT = TypeVar("ModelT", bound=Base)
SchemaT = TypeVar("SchemaT", bound=BaseModel)

_ALLOWED_SORTS: dict[str, set[str]] = {
    "providers": {"created_at", "updated_at", "name", "enabled"},
    "rules": {"created_at", "updated_at", "rule_id", "title", "enabled"},
    "projects": {"created_at", "updated_at", "name", "enabled"},
    "reviews": {"created_at", "updated_at", "status", "mr_iid", "finding_count"},
    "findings": {"created_at", "updated_at", "severity", "file_path", "fp_status"},
    "negative_examples": {"created_at", "updated_at", "rule_id"},
    "engines": {"created_at", "updated_at", "name", "enabled"},
}





def _unauthorized() -> HTTPException:
    """Build a consistent 401 response for admin bearer authentication failures."""

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid admin token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _require_admin_auth(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str:
    """Validate the admin bearer token before serving protected management APIs."""

    if authorization is None:
        raise _unauthorized()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    return _verify_token(token.strip())


def _verify_token(token: str) -> str:
    """Verify a standard JWT and return the authenticated subject."""

    settings = get_settings()
    try:
        payload = pyjwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except pyjwt.PyJWTError as exc:
        raise _unauthorized() from exc

    username = str(payload.get("sub", ""))
    expected_username = settings.admin_username
    if not hmac.compare_digest(username, expected_username):
        raise _unauthorized()
    return username


login_router = APIRouter(prefix="/api", tags=["admin"])
router = APIRouter(prefix="/api", tags=["admin"], dependencies=[Depends(_require_admin_auth)])

class Page(BaseModel):
    """Generic paginated response envelope for admin list endpoints."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[Any]
    total: int
    limit: int
    offset: int


class LoginRequest(BaseModel):
    """MVP single-account login payload."""

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    """MVP bearer token response."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class FalsePositiveMarkRequest(BaseModel):
    """Developer false-positive mark payload."""

    marked_by: str = Field(min_length=1, max_length=255)
    reason: str | None = None


class FalsePositiveReviewRequest(BaseModel):
    """Admin false-positive review payload."""

    reviewed_by: str = Field(min_length=1, max_length=255)
    note: str | None = None


@login_router.post("/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest) -> LoginResponse:
    """Authenticate the MVP admin account and return a signed bearer token."""

    settings = get_settings()
    expected_username = settings.admin_username
    expected_password = settings.admin_password.get_secret_value()
    if not hmac.compare_digest(payload.username, expected_username) or not hmac.compare_digest(
        payload.password,
        expected_password,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    expires_in = settings.jwt_expires_in
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    return LoginResponse(
        access_token=_sign_token(payload.username, expires_at),
        expires_in=expires_in,
    )


@router.get("/providers", response_model=Page)
async def list_providers(
    db: DbSession,
    enabled: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List LLM providers with paging, keyword filtering, and sorting."""

    stmt = select(Provider)
    if enabled is not None:
        stmt = stmt.where(Provider.enabled == enabled)
    if q:
        stmt = stmt.where(Provider.name.ilike(f"%{q}%"))
    return await _paginate(db, stmt, ProviderRead, "providers", sort, limit, offset)


@router.post("/providers", response_model=ProviderRead, status_code=status.HTTP_201_CREATED)
async def create_provider(payload: ProviderCreate, db: DbSession) -> ProviderRead:
    """Create an LLM provider configuration."""

    provider = Provider(**payload.model_dump())
    return await _create(db, provider, ProviderRead, "Provider already exists")


@router.get("/providers/{provider_id}", response_model=ProviderRead)
async def get_provider(provider_id: UUID, db: DbSession) -> ProviderRead:
    """Return one LLM provider by ID."""

    provider = await _get_or_404(db, Provider, provider_id, "Provider")
    return ProviderRead.model_validate(provider)


@router.patch("/providers/{provider_id}", response_model=ProviderRead)
async def update_provider(
    provider_id: UUID,
    payload: ProviderUpdate,
    db: DbSession,
) -> ProviderRead:
    """Update an LLM provider configuration."""

    provider = await _get_or_404(db, Provider, provider_id, "Provider")
    return await _update(db, provider, payload, ProviderRead)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: UUID, db: DbSession) -> None:
    """Delete an LLM provider configuration."""

    await _delete(db, Provider, provider_id, "Provider")


@router.get("/rules", response_model=Page)
async def list_rules(
    db: DbSession,
    enabled: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List review rules with filters and pagination."""

    stmt = select(Rule)
    if enabled is not None:
        stmt = stmt.where(Rule.enabled == enabled)
    if q:
        stmt = stmt.where((Rule.rule_id.ilike(f"%{q}%")) | (Rule.title.ilike(f"%{q}%")))
    return await _paginate(db, stmt, RuleRead, "rules", sort, limit, offset)


@router.post("/rules", response_model=RuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(payload: RuleCreate, db: DbSession) -> RuleRead:
    """Create a reusable review rule."""

    # rule_id 留空时从 title 自动生成 slug；显式传入则沿用（含冲突 -> 409）。
    rule_id = payload.rule_id
    if not rule_id:
        rule_id = await _generate_rule_slug(payload.title, db)
    data = payload.model_dump()
    data["rule_id"] = rule_id
    rule = Rule(**data)
    return await _create(db, rule, RuleRead, "Rule already exists")


@router.get("/rules/{rule_id}", response_model=RuleRead)
async def get_rule(rule_id: UUID, db: DbSession) -> RuleRead:
    """Return one review rule by ID."""

    rule = await _get_or_404(db, Rule, rule_id, "Rule")
    return RuleRead.model_validate(rule)


@router.patch("/rules/{rule_id}", response_model=RuleRead)
async def update_rule(rule_id: UUID, payload: RuleUpdate, db: DbSession) -> RuleRead:
    """Update a reusable review rule."""

    rule = await _get_or_404(db, Rule, rule_id, "Rule")
    return await _update(db, rule, payload, RuleRead)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: UUID, db: DbSession) -> None:
    """Delete a reusable review rule."""

    await _delete(db, Rule, rule_id, "Rule")


@router.get("/projects", response_model=Page)
async def list_projects(
    db: DbSession,
    enabled: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List GitLab project configurations."""

    stmt = _project_select()
    if enabled is not None:
        stmt = stmt.where(Project.enabled == enabled)
    if q:
        stmt = stmt.where(
            (Project.name.ilike(f"%{q}%")) | (Project.gitlab_project_id.ilike(f"%{q}%")),
        )
    return await _paginate(db, stmt, ProjectRead, "projects", sort, limit, offset)


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, db: DbSession) -> ProjectRead:
    """Create a project with optional nested rules and block policies.

    「安全默认」策略：``payload.rules is None`` 表示前端未显式传规则关联，
    此时后端自动把所有 ``enabled=true && severity_default=BLOCKER`` 的规则关联
    到新项目上——保证新项目开箱就有基础安全规则在跑。显式传 ``[]``
    代表用户 opt out，不做自动关联。显式传非空数组则以传入内容为准。
    """

    data = payload.model_dump(exclude={"rules", "block_policies"})
    project = Project(**data)
    if payload.rules is None:
        rules = await _default_blocker_project_rules(db)
    else:
        rules = list(payload.rules)
    _replace_project_rules(project, rules)
    _replace_block_policies(project, payload.block_policies or [])
    db.add(project)
    await _commit_or_400(db, "Project create failed")
    await db.refresh(project)
    return await _load_project_read(db, project.id)


@router.get("/projects/{project_id}", response_model=ProjectRead)
async def get_project(project_id: UUID, db: DbSession) -> ProjectRead:
    """Return one project configuration by ID."""

    return await _load_project_read(db, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
async def update_project(project_id: UUID, payload: ProjectUpdate, db: DbSession) -> ProjectRead:
    """Update a project and optionally replace nested rules and block policies."""

    project = await _get_or_404(db, Project, project_id, "Project", options=True)
    data = payload.model_dump(exclude_unset=True, exclude={"rules", "block_policies"})
    for field, value in data.items():
        setattr(project, field, value)
    if payload.rules is not None:
        _replace_project_rules(project, payload.rules)
    if payload.block_policies is not None:
        _replace_block_policies(project, payload.block_policies)
    await _commit_or_400(db, "Project update failed")
    return await _load_project_read(db, project_id)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: UUID, db: DbSession) -> None:
    """Delete a project configuration and dependent rules/policies/reviews."""

    await _delete(db, Project, project_id, "Project")


@router.get("/reviews", response_model=Page)
@router.get("/reviews/records", response_model=Page)
async def list_reviews(
    db: DbSession,
    project_id: UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    mr_iid: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List review records with management UI filters."""

    stmt = select(Review)
    if project_id is not None:
        stmt = stmt.where(Review.project_id == project_id)
    if status_filter:
        stmt = stmt.where(Review.status == status_filter)
    if mr_iid:
        stmt = stmt.where(Review.mr_iid == mr_iid)
    return await _paginate(
        db, stmt, ReviewRead, "reviews", sort, limit, offset, enrich=_review_to_read
    )


@router.post("/reviews/records", response_model=ReviewRead, status_code=status.HTTP_201_CREATED)
async def create_review_record(payload: ReviewCreate, db: DbSession) -> ReviewRead:
    """Create a review record for tests and internal admin seeding."""

    review = Review(**payload.model_dump())
    return await _create(db, review, ReviewRead, "Review create failed")


@router.get("/reviews/{review_id}", response_model=ReviewRead)
async def get_review_record(review_id: UUID, db: DbSession) -> ReviewRead:
    """Return one review record by ID."""

    review = await _get_or_404(db, Review, review_id, "Review")
    return _review_to_read(review)


@router.patch("/reviews/{review_id}", response_model=ReviewRead)
async def update_review_record(review_id: UUID, payload: ReviewUpdate, db: DbSession) -> ReviewRead:
    """Update a review record for internal admin correction."""

    review = await _get_or_404(db, Review, review_id, "Review")
    return await _update(db, review, payload, ReviewRead)


@router.delete("/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review_record(review_id: UUID, db: DbSession) -> None:
    """Delete one review record."""

    await _delete(db, Review, review_id, "Review")


@router.get("/findings", response_model=Page)
async def list_findings(
    db: DbSession,
    review_id: UUID | None = None,
    severity: str | None = None,
    fp_status: str | None = None,
    file_path: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List review findings with filters used by review detail and FP queue pages."""

    stmt = select(Finding)
    if review_id is not None:
        stmt = stmt.where(Finding.review_id == review_id)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    if fp_status:
        stmt = stmt.where(Finding.fp_status == fp_status)
    if file_path:
        stmt = stmt.where(Finding.file_path.ilike(f"%{file_path}%"))
    return await _paginate(
        db, stmt, FindingRead, "findings", sort, limit, offset,
        enrich=_finding_to_read,
    )


@router.post("/findings", response_model=FindingRead, status_code=status.HTTP_201_CREATED)
async def create_finding(payload: FindingCreate, db: DbSession) -> FindingRead:
    """Create a finding for tests and internal admin seeding."""

    finding = Finding(**payload.model_dump())
    return await _create(db, finding, FindingRead, "Finding create failed")


@router.get("/findings/{finding_id}", response_model=FindingRead)
async def get_finding(finding_id: UUID, db: DbSession) -> FindingRead:
    """Return one finding by ID."""

    finding = await _get_or_404(db, Finding, finding_id, "Finding")
    return _finding_to_read(finding)


@router.patch("/findings/{finding_id}", response_model=FindingRead)
async def update_finding(finding_id: UUID, payload: FindingUpdate, db: DbSession) -> FindingRead:
    """Update one finding for internal admin correction."""

    finding = await _get_or_404(db, Finding, finding_id, "Finding")
    return await _update(db, finding, payload, FindingRead)


@router.delete("/findings/{finding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_finding(finding_id: UUID, db: DbSession) -> None:
    """Delete one finding."""

    await _delete(db, Finding, finding_id, "Finding")


@router.post("/findings/{finding_id}/false-positive", response_model=FindingRead)
async def mark_false_positive(
    finding_id: UUID,
    payload: FalsePositiveMarkRequest,
    db: DbSession,
) -> FindingRead:
    """Mark a finding as a pending false-positive candidate."""

    finding = await _get_or_404(db, Finding, finding_id, "Finding")
    finding.fp_status = "PENDING"
    finding.fp_marked_by = payload.marked_by
    finding.fp_marked_reason = payload.reason
    finding.fp_marked_at = datetime.now(UTC)
    finding.fp_reviewed_by = None
    finding.fp_reviewed_at = None
    finding.fp_review_note = None
    await _commit_or_400(db, "False-positive mark failed")
    # refresh 后关系可能已 stale；显式声明 attribute_names 强制重新加载 review，
    # 保证 _finding_to_read 能取到 review / project 上下文（正确性优先）。
    await db.refresh(finding, attribute_names=["review"])
    return _finding_to_read(finding)


@router.get("/false-positives/pending", response_model=Page)
async def list_pending_false_positives(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "created_at",
) -> Page:
    """List pending false-positive candidates awaiting admin review."""

    stmt = select(Finding).where(Finding.fp_status == "PENDING")
    return await _paginate(db, stmt, FindingRead, "findings", sort, limit, offset)


@router.post("/false-positives/{finding_id}/confirm", response_model=FindingRead)
async def confirm_false_positive(
    finding_id: UUID,
    payload: FalsePositiveReviewRequest,
    db: DbSession,
) -> FindingRead:
    """Confirm a false-positive and persist it as a negative prompt example."""

    finding = await _get_pending_finding(db, finding_id)
    finding.fp_status = "CONFIRMED"
    finding.fp_reviewed_by = payload.reviewed_by
    finding.fp_reviewed_at = datetime.now(UTC)
    finding.fp_review_note = payload.note
    review = await db.get(Review, finding.review_id)
    existing_code = finding.existing_code or finding.description or finding.title
    db.add(
        NegativeExample(
            rule_id=finding.rule_id,
            project_id=review.project_id if review else None,
            code_snippet=existing_code,
            explanation=payload.note or finding.fp_marked_reason,
            source_finding_id=finding.id,
            approved_by=payload.reviewed_by,
            approved_at=datetime.now(UTC),
        ),
    )
    await _commit_or_400(db, "False-positive confirm failed")
    await db.refresh(finding, attribute_names=["review"])
    return _finding_to_read(finding)


@router.post("/false-positives/{finding_id}/reject", response_model=FindingRead)
async def reject_false_positive(
    finding_id: UUID,
    payload: FalsePositiveReviewRequest,
    db: DbSession,
) -> FindingRead:
    """Reject a false-positive candidate and retain review audit fields."""

    finding = await _get_pending_finding(db, finding_id)
    finding.fp_status = "REJECTED"
    finding.fp_reviewed_by = payload.reviewed_by
    finding.fp_reviewed_at = datetime.now(UTC)
    finding.fp_review_note = payload.note
    await _commit_or_400(db, "False-positive reject failed")
    await db.refresh(finding, attribute_names=["review"])
    return _finding_to_read(finding)


@router.get("/negative-examples", response_model=Page)
async def list_negative_examples(
    db: DbSession,
    rule_id: str | None = None,
    project_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List approved negative examples generated by false-positive review."""

    stmt = select(NegativeExample)
    if rule_id:
        stmt = stmt.where(NegativeExample.rule_id == rule_id)
    if project_id is not None:
        stmt = stmt.where(NegativeExample.project_id == project_id)
    return await _paginate(db, stmt, NegativeExampleRead, "negative_examples", sort, limit, offset)


@router.get("/engines/configs", response_model=Page)
async def list_engine_configs(
    db: DbSession,
    enabled: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = "-created_at",
) -> Page:
    """List persisted engine configurations."""

    stmt = select(Engine)
    if enabled is not None:
        stmt = stmt.where(Engine.enabled == enabled)
    if q:
        stmt = stmt.where(Engine.name.ilike(f"%{q}%"))
    return await _paginate(db, stmt, EngineRead, "engines", sort, limit, offset)


@router.post("/engines/configs", response_model=EngineRead, status_code=status.HTTP_201_CREATED)
async def create_engine_config(payload: EngineCreate, db: DbSession) -> EngineRead:
    """Create a persisted engine configuration."""

    engine = Engine(**payload.model_dump())
    return await _create(db, engine, EngineRead, "Engine config already exists")


@router.get("/engines/configs/{engine_id}", response_model=EngineRead)
async def get_engine_config(engine_id: UUID, db: DbSession) -> EngineRead:
    """Return one persisted engine configuration."""

    engine = await _get_or_404(db, Engine, engine_id, "Engine config")
    return EngineRead.model_validate(engine)


@router.patch("/engines/configs/{engine_id}", response_model=EngineRead)
async def update_engine_config(engine_id: UUID, payload: EngineUpdate, db: DbSession) -> EngineRead:
    """Update a persisted engine configuration, including enable/disable."""

    engine = await _get_or_404(db, Engine, engine_id, "Engine config")
    return await _update(db, engine, payload, EngineRead)


@router.delete("/engines/configs/{engine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_engine_config(engine_id: UUID, db: DbSession) -> None:
    """Delete a persisted engine configuration."""

    await _delete(db, Engine, engine_id, "Engine config")


async def _paginate(
    db: AsyncSession,
    stmt: Select[tuple[ModelT]],
    schema: type[SchemaT],
    sort_group: str,
    sort: str,
    limit: int,
    offset: int,
    enrich: Callable[[ModelT], SchemaT] | None = None,
) -> Page:
    """Apply count, sorting, offset, and limit to a select statement.

    分页读取通过 Repository 层完成：``BaseRepository`` 暴露的 ``session`` 与
    ``execute`` 均可复用；这里保留 ``select`` 语句参数是因为不同接口
    需要自定义 join / eager-load / where 过滤，不宜在 Repository 里穷举。

    ``enrich`` 可选：对每行 ORM 对象做自定义封装（如 Review 需附带 project_name /
    rules_used 时），不传则退回 ``schema.model_validate`` 默认映射。
    """

    ordered_stmt = _apply_sort(stmt, sort_group, sort)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    result = await db.execute(ordered_stmt.offset(offset).limit(limit))
    items = [
        (enrich(row) if enrich else schema.model_validate(row))
        for row in result.scalars().unique().all()
    ]
    return Page(items=items, total=total or 0, limit=limit, offset=offset)


async def _create(
    db: AsyncSession,
    model: ModelT,
    schema: type[SchemaT],
    error_message: str,
) -> SchemaT:
    """Persist one ORM object and convert it to a response schema.

    走 ``BaseRepository.add`` 挂载对象，最终 commit 交给 ``_commit_or_400`` 处理，
    这样错误映射（IntegrityError→409、其它 SQLAlchemyError→500）保持不变。
    """

    repo: BaseRepository[ModelT] = BaseRepository(db)
    repo.model = type(model)
    await repo.add(model, flush=False)
    await _commit_or_400(db, error_message)
    await db.refresh(model)
    return schema.model_validate(model)


async def _update(
    db: AsyncSession,
    model: ModelT,
    payload: BaseModel,
    schema: type[SchemaT],
) -> SchemaT:
    """Patch an ORM object from a Pydantic payload."""

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(model, field, value)
    await _commit_or_400(db, f"{model.__class__.__name__} update failed")
    await db.refresh(model)
    return schema.model_validate(model)


async def _delete(db: AsyncSession, model_type: type[ModelT], model_id: UUID, label: str) -> None:
    """Delete an ORM object by primary key or return 404."""

    model = await _get_or_404(db, model_type, model_id, label)
    repo: BaseRepository[ModelT] = BaseRepository(db)
    repo.model = model_type
    await repo.delete(model, flush=False)
    await _commit_or_400(db, f"{label} delete failed")


async def _get_or_404(
    db: AsyncSession,
    model_type: type[ModelT],
    model_id: UUID,
    label: str,
    *,
    options: bool = False,
) -> ModelT:
    """Fetch an ORM object by ID or raise a 404 response.

    Project 类型且要求带 relations 时，走 ``ProjectRepository.get_with_relations``
    以复用 eager-load 语句；其它模型走通用 ``BaseRepository.get``。
    """

    model: Base | None
    if model_type is Project and options:
        project_repo = ProjectRepository(db)
        model = await project_repo.get_with_relations(model_id)
    else:
        repo: BaseRepository[ModelT] = BaseRepository(db)
        repo.model = model_type
        model = await repo.get(model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return cast(ModelT, model)


async def _commit_or_400(db: AsyncSession, detail: str) -> None:
    """提交数据库变更，并把持久化失败映射到语义清晰的 HTTP 错误。

    - IntegrityError：唯一约束 / 外键冲突 → 409 Conflict，保留传入的 detail（这就是重名/外键冲突）。
    - 其他 SQLAlchemyError（如加密失败包成的 StatementError）→ 500 Internal Server Error，
      detail 附带根本异常类名与摘要，便于排查，而不是误报成"已存在"。
    """

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        # DBAPIError 有 orig 属性指向底层驱动异常；其它 SQLAlchemyError 没有 orig。
        # 用 getattr 兼容两种情况，并对 mypy 友好。
        orig = getattr(exc, "orig", None)
        internal_detail = (
            f"{detail} (internal error: {exc.__class__.__name__}: "
            f"{str(orig or exc)[:200]})"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=internal_detail,
        ) from exc


def _apply_sort(stmt: Select[tuple[ModelT]], sort_group: str, sort: str) -> Select[tuple[ModelT]]:
    """Apply allow-listed sorting to a query."""

    descending = sort.startswith("-")
    field_name = sort[1:] if descending else sort
    allowed = _ALLOWED_SORTS.get(sort_group, set())
    if field_name not in allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid sort field")
    entity = stmt.column_descriptions[0].get("entity")
    if entity is None or not hasattr(entity, field_name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid sort field")
    column = getattr(entity, field_name)
    return stmt.order_by(desc(column) if descending else asc(column))


def _project_select() -> Select[tuple[Project]]:
    """Build project select with nested relationships loaded for response serialization."""

    return select(Project).options(
        selectinload(Project.project_rules),
        selectinload(Project.block_policies),
    )


async def _load_project_read(db: AsyncSession, project_id: UUID) -> ProjectRead:
    """Load and serialize a project with nested rules and policies."""

    result = await db.execute(_project_select().where(Project.id == project_id))
    project = result.scalars().unique().one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return ProjectRead.model_validate(project)


def _replace_project_rules(project: Project, rules: Sequence[ProjectRuleCreate]) -> None:
    """Replace project-rule associations from request payload."""

    project.project_rules = [
        ProjectRule(**rule.model_dump(exclude={"project_id"}))
        for rule in rules
    ]


async def _default_blocker_project_rules(db: AsyncSession) -> list[ProjectRuleCreate]:
    """默认关联规则：所有 ``enabled && severity_default='BLOCKER'`` 的规则。

    只在 ``ProjectCreate.rules is None`` 时（前端未显式传规则关联）触发，
    保证新项目「安全默认」——一进来就有基础安全规则在跑。

    - ``enabled=True``：跟随 payload 默认；
    - ``severity_override=None``：使用规则自己的 severity_default，不覆盖。
    - 未来 seed 更多 BLOCKER 规则会自动生效。

    只查 ID 减少内存占用；`RuleRepository` 目前没有专用方法就直接构造语句。
    """

    stmt = select(Rule.id).where(
        Rule.enabled.is_(True),
        Rule.severity_default == "BLOCKER",
    )
    result = await db.execute(stmt)
    rule_ids = [row[0] for row in result.all()]
    return [
        ProjectRuleCreate(rule_id=rule_id, enabled=True, severity_override=None)
        for rule_id in rule_ids
    ]


def _replace_block_policies(project: Project, policies: Sequence[ProjectBlockPolicyCreate]) -> None:
    """Replace branch block policies from request payload ordered by priority."""

    project.block_policies = [
        ProjectBlockPolicy(**policy.model_dump(exclude={"project_id"}))
        for policy in sorted(policies, key=lambda item: item.priority)
    ]


async def _generate_rule_slug(title: str, db: AsyncSession) -> str:
    """从标题自动生成唯一的 rule_id slug。

    - 标题含中文 -> 用 ``rule-<uuid8>`` 兜底，避免 slug 出现非 ASCII；
    - 否则按非「英文/数字/空格/-」字符清洗后小写、空格转 ``-`` 生成 slug；
    - 清洗后为空 -> 退回 ``rule-<uuid8>``；
    - 与已有 rule_id 冲突 -> 追加 ``-2``、``-3`` 后缀直至唯一。
    """

    if re.search("[一-鿿]", title):
        return f"rule-{uuid4().hex[:8]}"
    slug = re.sub(r"[^\w\s-]", "", title).strip().lower().replace(" ", "-")
    if not slug:
        return f"rule-{uuid4().hex[:8]}"
    repo = RuleRepository(db)
    candidate = slug
    suffix = 2
    while await repo.get_by_rule_id(candidate):
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


def _review_to_read(review: Review) -> ReviewRead:
    """构建带项目名与使用规则的 ReviewRead。

    - ``project_name`` 从 ``review.project.name`` 读（relationship lazy=selectin）；
    - ``rules_used`` 从 ``review.findings`` 聚合 ``rule_id`` 并去重，保持首次出现顺序。

    Review.project 为非空外键（ondelete=CASCADE），故 project 关系必存在，无需 None 兜底。
    """

    read = ReviewRead.model_validate(review)
    read.project_name = review.project.name
    rules_used: list[str] = []
    seen: set[str] = set()
    for finding in review.findings:
        if finding.rule_id not in seen:
            seen.add(finding.rule_id)
            rules_used.append(finding.rule_id)
    read.rules_used = rules_used
    return read


def _finding_to_read(finding: Finding) -> FindingRead:
    """构建带 MR / 项目上下文的 FindingRead。

    - ``project_name`` / ``project_id``：从 ``finding.review.project`` 关系读；
    - ``mr_iid`` / ``review_created_at``：从 ``finding.review`` 读；
    - ``mr_title``：Review 表未落库该列（见 FindingRead 注释），保留 None
      以待未来落库后一次性联通。

    ``Finding.review`` 与 ``Review.project`` 都是 ``lazy=selectin``（分别见
    ``app/models/finding.py`` 和 ``app/models/review.py``），list 场景一次性
    加载，无 N+1；单条 refresh 后的调用方需要自行 ``refresh(attribute_names=
    ["review"])`` 以避免 relationship stale。
    """

    read = FindingRead.model_validate(finding)
    review = finding.review
    if review is not None:
        read.mr_iid = review.mr_iid
        read.review_created_at = review.created_at
        if review.project is not None:
            read.project_name = review.project.name
            read.project_id = review.project.id
    return read


async def _get_pending_finding(db: AsyncSession, finding_id: UUID) -> Finding:
    """Return a finding that is currently pending false-positive review."""

    finding = await _get_or_404(db, Finding, finding_id, "Finding")
    if finding.fp_status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finding is not pending false-positive review",
        )
    return finding


def _sign_token(username: str, expires_at: datetime) -> str:
    """Create a standard JWT signed with the configured secret and algorithm."""

    settings = get_settings()
    payload = {
        "sub": username,
        "exp": int(expires_at.timestamp()),
        "iat": int(datetime.now(UTC).timestamp()),
    }
    return pyjwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
