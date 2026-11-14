"""Admin HTTP API — one module per resource family.

Layout
------
``deps``      FastAPI dependencies: bearer auth, RBAC permission guards, JWT helpers
``common``    response envelope (``Page``), allow-listed sorting, thin CRUD helpers
``auth``      ``/auth/login`` and ``/auth/me``
``providers`` LLM provider configuration
``rules``     review rule catalog
``projects``  GitLab projects + notification channels + project user mappings
``reviews``   review records (MR / push / commit), recent panel, agent trace
``findings``  findings, false-positive queue, negative examples
``engines``   persisted engine configuration
``prompts``   global prompt and project-level negative prompt
``rbac``      users, project assignment, roles

Every domain module declares its own ``APIRouter`` with the ``/api`` prefix and
the bearer-auth dependency, so a route's guard requirements stay visible in the
module that owns it. :data:`router` aggregates the non-RBAC routers to keep
``main`` to a single ``include_router`` call; :data:`users_router` stays separate
to preserve the historical RBAC grouping.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.admin import (
    auth,
    findings,
    projects,
    prompts,
    providers,
    rbac,
    reviews,
    rules,
)
from api.admin.engines import router as engines_router

router = APIRouter()
for _domain_router in (
    providers.router,
    rules.router,
    projects.router,
    reviews.router,
    findings.router,
    engines_router,
    prompts.router,
):
    router.include_router(_domain_router)

login_router = auth.login_router
users_router = rbac.users_router

__all__ = ["login_router", "router", "users_router"]
