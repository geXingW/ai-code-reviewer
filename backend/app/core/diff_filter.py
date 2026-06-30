"""GitLab merge request diff filtering helpers.

The orchestrator should only send reviewable text diffs to engines. This module
keeps that decision deterministic and independently testable so project-level
settings can later feed the same filter without changing engine contracts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any


@dataclass(frozen=True)
class DiffFilterConfig:
    """Configuration controlling which GitLab change entries are reviewable.

    Attributes:
        ignore_paths: Glob patterns matched against ``new_path`` and ``old_path``.
        max_diff_bytes: Maximum UTF-8 byte length of a single file diff.
    """

    ignore_paths: Sequence[str] = field(default_factory=tuple)
    max_diff_bytes: int = 200_000


def filter_gitlab_changes(
    raw_changes: Iterable[object],
    config: DiffFilterConfig | None = None,
) -> list[dict[str, Any]]:
    """Return GitLab change entries that are safe and useful to review.

    Malformed entries, ignored paths, binary files, deleted files, empty diffs,
    and oversized diffs are skipped. The original dict shape is preserved for
    downstream conversion into ``DiffHunk`` objects.
    """

    cfg = config or DiffFilterConfig()
    filtered: list[dict[str, Any]] = []
    for item in raw_changes:
        if not isinstance(item, Mapping):
            continue
        change = dict(item)
        if _should_skip_change(change, cfg):
            continue
        filtered.append(change)
    return filtered


def _should_skip_change(change: dict[str, Any], config: DiffFilterConfig) -> bool:
    """Return True when one GitLab change should not be reviewed."""

    new_path = str(change.get("new_path") or "")
    old_path = str(change.get("old_path") or "")
    diff = str(change.get("diff") or "")
    if not new_path and not old_path:
        return True
    if not diff.strip():
        return True
    if bool(change.get("binary", False)):
        return True
    if bool(change.get("deleted_file", False)):
        return True
    if len(diff.encode("utf-8")) > config.max_diff_bytes:
        return True
    return _matches_any_path(new_path, config.ignore_paths) or _matches_any_path(
        old_path,
        config.ignore_paths,
    )


def _matches_any_path(path: str, patterns: Sequence[str]) -> bool:
    """Return True when ``path`` matches any configured glob pattern."""

    if not path:
        return False
    return any(fnmatchcase(path, pattern) for pattern in patterns)
