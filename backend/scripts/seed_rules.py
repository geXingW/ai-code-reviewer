"""从 docs/rules-catalog.json 幂等 seed 基础规则库。

用法：

    cd backend
    python -m scripts.seed_rules --dry-run     # 预览将要创建/更新的规则
    python -m scripts.seed_rules               # 幂等写入（已存在的 rule_id 跳过）
    python -m scripts.seed_rules --overwrite   # 覆盖已存在同 rule_id 的规则字段

按 ``rule_id`` 幂等匹配：不存在则创建、存在则按需覆盖（``--overwrite`` 才更新）。
不改动 ``project_rules`` 关联表——规则创建后需要在管理后台或通过 API 显式关联到项目。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.db import AsyncSessionLocal
from app.models.rule import Rule
from app.repositories.rule import RuleRepository

DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "docs" / "rules-catalog.json"

# 期望的字段集合（其它字段会被忽略，避免 JSON 里多写字段导致构造失败）。
_ALLOWED_FIELDS = {
    "rule_id",
    "title",
    "prompt_snippet",
    "severity_default",
    "languages",
    "path_patterns",
    "enabled",
    "grace_period_until",
}


def _load_catalog(path: Path) -> list[dict[str, Any]]:
    """读取并校验 rules-catalog.json。"""

    if not path.exists():
        raise FileNotFoundError(f"catalog not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("catalog root must be a JSON array")
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for i, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ValueError(f"rule[{i}] is not an object")
        rid = raw.get("rule_id")
        if not rid:
            raise ValueError(f"rule[{i}] missing rule_id")
        if rid in seen:
            raise ValueError(f"duplicate rule_id in catalog: {rid}")
        seen.add(rid)
        for key in ("title", "prompt_snippet", "severity_default"):
            if not raw.get(key):
                raise ValueError(f"{rid} missing required field: {key}")
        cleaned.append({k: v for k, v in raw.items() if k in _ALLOWED_FIELDS})
    return cleaned


async def _seed(
    rules: list[dict[str, Any]], *, dry_run: bool, overwrite: bool
) -> tuple[int, int, int]:
    """执行 seed，返回 (created, updated, skipped)。"""

    created = updated = skipped = 0
    async with AsyncSessionLocal() as session:
        repo = RuleRepository(session)
        for payload in rules:
            existing = await repo.get_by_rule_id(payload["rule_id"])
            if existing is None:
                if dry_run:
                    print(f"[dry-run] CREATE {payload['rule_id']}")
                else:
                    rule = Rule(**payload)
                    session.add(rule)
                    try:
                        await session.flush()
                    except IntegrityError as exc:
                        await session.rollback()
                        raise RuntimeError(
                            f"failed to insert {payload['rule_id']}: {exc}"
                        ) from exc
                    print(f"CREATED {payload['rule_id']}")
                created += 1
                continue

            if not overwrite:
                print(f"SKIP    {payload['rule_id']} (exists, use --overwrite to update)")
                skipped += 1
                continue

            changed = False
            for key, value in payload.items():
                if key == "rule_id":
                    continue
                if getattr(existing, key, None) != value:
                    setattr(existing, key, value)
                    changed = True
            if not changed:
                print(f"SKIP    {payload['rule_id']} (no change)")
                skipped += 1
                continue

            if dry_run:
                print(f"[dry-run] UPDATE {payload['rule_id']}")
            else:
                await session.flush()
                print(f"UPDATED {payload['rule_id']}")
            updated += 1

        if dry_run:
            await session.rollback()
        else:
            await session.commit()

    return created, updated, skipped


def _parse_args() -> argparse.Namespace:
    doc = (__doc__ or "").strip().splitlines()
    parser = argparse.ArgumentParser(description=doc[0] if doc else "")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help=f"规则库 JSON 路径（默认 {DEFAULT_CATALOG}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要执行的动作，不写库",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="已存在的同 rule_id 覆盖字段（默认跳过）",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        rules = _load_catalog(args.catalog)
    except (FileNotFoundError, ValueError) as exc:
        print(f"catalog error: {exc}", file=sys.stderr)
        return 2

    print(f"Loaded {len(rules)} rules from {args.catalog}")
    try:
        created, updated, skipped = asyncio.run(
            _seed(rules, dry_run=args.dry_run, overwrite=args.overwrite),
        )
    except RuntimeError as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1

    mode = "dry-run" if args.dry_run else "committed"
    print(
        f"\nDone ({mode}). created={created} updated={updated} skipped={skipped}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
