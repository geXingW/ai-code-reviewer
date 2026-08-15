"""Tests for diff utility functions in review_orchestrator."""

from __future__ import annotations

from services.review_orchestrator import _is_line_number_valid_for_current_diff


class TestIsLineNumberValidForCurrentDiff:
    """Test line number validation against GitLab diff payload."""

    def test_line_in_hunk_range_returns_true(self) -> None:
        """Line number falling within a diff hunk range should be valid."""

        changes_payload = {
            "changes": [
                {
                    "new_path": "app/main.py",
                    "deleted_file": False,
                    "diff": "@@ -10,5 +10,5 @@\n line1\n line2\n line3\n line4\n line5\n",
                },
            ],
        }

        # Line 12 is within hunk range +10,5 (lines 10-14)
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 10) is True
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 12) is True
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 14) is True

    def test_line_outside_hunk_range_returns_false(self) -> None:
        """Line number outside any diff hunk range should be invalid."""

        changes_payload = {
            "changes": [
                {
                    "new_path": "app/main.py",
                    "deleted_file": False,
                    "diff": "@@ -10,5 +10,5 @@\n line1\n line2\n line3\n line4\n line5\n",
                },
            ],
        }

        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 1) is False
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 9) is False
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 15) is False

    def test_deleted_file_returns_false(self) -> None:
        """Deleted file should always return False (no code to comment on)."""

        changes_payload = {
            "changes": [
                {
                    "new_path": "app/deleted.py",
                    "deleted_file": True,
                    "diff": "@@ -1,5 +0,0 @@\n line1\n",
                },
            ],
        }

        assert _is_line_number_valid_for_current_diff(changes_payload, "app/deleted.py", 1) is False

    def test_file_not_in_changes_returns_false(self) -> None:
        """File not present in changes payload should return False."""

        changes_payload = {
            "changes": [{"new_path": "app/main.py", "deleted_file": False, "diff": ""}]
        }
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/other.py", 1) is False

    def test_empty_diff_returns_false(self) -> None:
        """Empty diff string should return False (no hunks to check)."""

        changes_payload = {
            "changes": [
                {"new_path": "app/main.py", "deleted_file": False, "diff": ""},
            ],
        }

        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 1) is False

    def test_single_line_hunk_works(self) -> None:
        """Hunk with single line count (",1" omitted in diff header)."""

        # Git omits ",1" when count is 1: @@ -5 +5 @@
        changes_payload = {
            "changes": [
                {
                    "new_path": "app/main.py",
                    "deleted_file": False,
                    "diff": "@@ -5 +5 @@\n single line change\n",
                },
            ],
        }

        # New range is line 5, single line
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 5) is True
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 4) is False
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 6) is False

    def test_multi_hunk_line_in_first_hunk_returns_true(self) -> None:
        """多 hunk：行号在第一个 hunk 范围内有效（现有行为不变）。"""

        changes_payload = {
            "changes": [
                {
                    "new_path": "app/main.py",
                    "deleted_file": False,
                    "diff": (
                        "@@ -10,5 +10,5 @@\n"
                        " line1\n line2\n line3\n line4\n line5\n"
                        "@@ -50,5 +51,5 @@\n"
                        " line6\n line7\n line8\n line9\n line10\n"
                    ),
                },
            ],
        }

        # 第一个 hunk 新文件行号范围 10-14
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 12) is True

    def test_multi_hunk_line_in_second_hunk_returns_true(self) -> None:
        """多 hunk：行号在第二个 hunk 范围内也应有效。"""

        changes_payload = {
            "changes": [
                {
                    "new_path": "app/main.py",
                    "deleted_file": False,
                    "diff": (
                        "@@ -10,5 +10,5 @@\n"
                        " line1\n line2\n line3\n line4\n line5\n"
                        "@@ -50,5 +51,5 @@\n"
                        " line6\n line7\n line8\n line9\n line10\n"
                    ),
                },
            ],
        }

        # 第二个 hunk 新文件行号范围 51-55
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 51) is True
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 55) is True

    def test_multi_hunk_line_between_hunks_returns_false(self) -> None:
        """多 hunk：行号落在两个 hunk 之间（未改动区域）应无效。"""

        changes_payload = {
            "changes": [
                {
                    "new_path": "app/main.py",
                    "deleted_file": False,
                    "diff": (
                        "@@ -10,5 +10,5 @@\n"
                        " line1\n line2\n line3\n line4\n line5\n"
                        "@@ -50,5 +51,5 @@\n"
                        " line6\n line7\n line8\n line9\n line10\n"
                    ),
                },
            ],
        }

        # 第一个 hunk 结束于 14，第二个 hunk 从 51 开始
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 15) is False
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 30) is False
        assert _is_line_number_valid_for_current_diff(changes_payload, "app/main.py", 50) is False
