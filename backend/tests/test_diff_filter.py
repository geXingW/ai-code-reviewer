"""Tests for diff filtering before engine execution."""

from __future__ import annotations

from app.core.diff_filter import DiffFilterConfig, filter_gitlab_changes


def test_filter_gitlab_changes_skips_ignored_binary_deleted_and_large_files() -> None:
    """Only reviewable text changes should be forwarded to engines."""

    changes = [
        {
            "new_path": "app/main.py",
            "old_path": "app/main.py",
            "diff": "@@ -1 +1 @@\n-print('old')\n+print('new')\n",
            "new_file": False,
            "deleted_file": False,
            "binary": False,
        },
        {
            "new_path": "dist/bundle.js",
            "old_path": "dist/bundle.js",
            "diff": "+generated\n",
            "deleted_file": False,
            "binary": False,
        },
        {
            "new_path": "image.png",
            "old_path": "image.png",
            "diff": "",
            "deleted_file": False,
            "binary": True,
        },
        {
            "new_path": "old.py",
            "old_path": "old.py",
            "diff": "-removed\n",
            "deleted_file": True,
            "binary": False,
        },
        {
            "new_path": "large.txt",
            "old_path": "large.txt",
            "diff": "+" + ("x" * 40),
            "deleted_file": False,
            "binary": False,
        },
    ]

    filtered = filter_gitlab_changes(
        changes,
        DiffFilterConfig(ignore_paths=("dist/**",), max_diff_bytes=40),
    )

    assert [item["new_path"] for item in filtered] == ["app/main.py"]


def test_filter_gitlab_changes_handles_malformed_payload_items() -> None:
    """Malformed GitLab change entries should be ignored, not crash the review."""

    filtered = filter_gitlab_changes(
        [None, "bad", {"new_path": "ok.py", "diff": "+x\n"}],
        DiffFilterConfig(),
    )

    assert filtered == [{"new_path": "ok.py", "diff": "+x\n"}]


def test_filter_gitlab_changes_limits_full_diff_bytes() -> None:
    """Full diff bytes should be limited before the engine receives the diff."""

    filtered = filter_gitlab_changes(
        [
            {
                "new_path": "context-heavy.py",
                "old_path": "context-heavy.py",
                "diff": "@@ -1,4 +1,4 @@\n" + (" context\n" * 20) + "-old\n+new\n",
                "deleted_file": False,
                "binary": False,
            }
        ],
        DiffFilterConfig(max_diff_bytes=80),
    )

    assert filtered == []


def test_filter_gitlab_changes_counts_utf8_bytes_not_characters() -> None:
    """Multi-byte UTF-8 diffs should be limited by bytes, not character count."""

    diff = "@@ -1 +1 @@\n-old\n+中文🙂\n"

    filtered = filter_gitlab_changes(
        [
            {
                "new_path": "i18n.py",
                "old_path": "i18n.py",
                "diff": diff,
                "deleted_file": False,
                "binary": False,
            }
        ],
        DiffFilterConfig(max_diff_bytes=len(diff)),
    )

    assert filtered == []
