"""Regression tests for :mod:`engines.finding_parsing`.

llm-direct 与 llm-agent 共用的解析层：JSON 容错、finding 规范化、
误报历史过滤与来源标签。行号定位细节（多 hunk 语义）的既有回归在
``test_diff_hunk_line_numbers.py``。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from engines.finding_parsing import (
    clamp_confidence,
    loads_model_json,
    matches_false_positive_history,
    optional_int,
    optional_str,
    parse_findings_from_response,
    tag_finding_source,
)
from engines.types import (
    DiffHunk,
    Finding,
    FindingSource,
    ReviewContext,
    ReviewHistoryItem,
    RuleSpec,
)


def _hunk() -> DiffHunk:
    return DiffHunk(
        file_path="app/auth.py",
        old_path="app/auth.py",
        new_start=10,
        new_lines=3,
        old_start=10,
        old_lines=2,
        content="@@ -10,2 +10,3 @@\n context\n+print(user.password)\n return token\n",
    )


def _ctx(history: list[ReviewHistoryItem] | None = None) -> ReviewContext:
    return ReviewContext(
        review_id=uuid4(),
        project_id=uuid4(),
        mr_iid="1",
        source_branch="f/x",
        target_branch="master",
        source_commit_sha="a",
        target_commit_sha="b",
        diff_hunks=[_hunk()],
        rules=[
            RuleSpec(
                id=uuid4(),
                rule_id="no-secret-logging",
                title="No secret logging",
                description="desc",
                severity="BLOCKER",
            )
        ],
        history=history or [],
    )


class TestLoadsModelJson:
    def test_plain_json(self) -> None:
        assert loads_model_json('{"findings": []}') == {"findings": []}

    def test_whole_response_fenced_block(self) -> None:
        text = '```json\n{"findings": []}\n```'
        assert loads_model_json(text) == {"findings": []}

    def test_embedded_fence_in_string_value_still_parses(self) -> None:
        text = '{"findings": [{"suggestion": "use ```python blocks"}]}'
        assert loads_model_json(text)["findings"][0]["suggestion"].startswith("use")

    def test_non_json_raises(self) -> None:
        with pytest.raises(ValueError):
            loads_model_json("not json at all")


class TestNormalisation:
    def test_parse_findings_resolves_line_via_existing_code(self) -> None:
        response = (
            '{"findings": [{"file_path": "app/auth.py", '
            '"rule_id": "no-secret-logging", "severity": "BLOCKER", '
            '"title": "Password printed", "existing_code": "print(user.password)"}]}'
        )
        findings = parse_findings_from_response(response, _ctx())
        assert findings[0].line_number == 11
        assert findings[0].source == FindingSource.USER_RULE

    def test_findings_on_unknown_files_dropped(self) -> None:
        response = (
            '{"findings": [{"file_path": "other.py", "line_number": 1, '
            '"rule_id": "x", "severity": "WARNING", "title": "nope"}]}'
        )
        assert parse_findings_from_response(response, _ctx()) == []

    def test_findings_outside_added_lines_dropped(self) -> None:
        response = (
            '{"findings": [{"file_path": "app/auth.py", "line_number": 99, '
            '"rule_id": "x", "severity": "WARNING", "title": "bad line"}]}'
        )
        assert parse_findings_from_response(response, _ctx()) == []

    def test_invalid_top_level_shape_returns_empty(self) -> None:
        assert parse_findings_from_response('{"findings": "not-a-list"}', _ctx()) == []


class TestHistoryFilterAndTags:
    def test_history_confirmed_false_positive_suppressed(self) -> None:
        history = [
            ReviewHistoryItem(
                rule_id="no-secret-logging",
                file_path="app/auth.py",
                line_number=11,
                title="Password printed",
                description=None,
                review_note="ok",
                confirmed_at="2026-09-01T00:00:00Z",
            )
        ]
        finding = Finding(
            file_path="app/auth.py",
            line_number=11,
            rule_id="no-secret-logging",
            severity="BLOCKER",
            title="Password printed",
        )
        assert matches_false_positive_history(finding, history) is True

    def test_unknown_rule_tagged_llm_inferred(self) -> None:
        finding = Finding(
            file_path="app/auth.py",
            line_number=11,
            rule_id="custom-idea",
            severity="WARNING",
            title="whatever",
        )
        tagged = tag_finding_source(finding, _ctx())
        assert tagged.source == FindingSource.LLM_INFERRED


class TestScalarHelpers:
    def test_optional_str(self) -> None:
        assert optional_str("  x ") == "x"
        assert optional_str("   ") is None
        assert optional_str(None) is None
        assert optional_str(42) == "42"

    def test_optional_int(self) -> None:
        assert optional_int("7") == 7
        assert optional_int(0) is None
        assert optional_int(-1) is None
        assert optional_int(True) is None
        assert optional_int("abc") is None

    def test_clamp_confidence(self) -> None:
        assert clamp_confidence(2) == 1.0
        assert clamp_confidence(-1) == 0.0
        assert clamp_confidence("bad") == 0.0
        assert clamp_confidence(0.42) == 0.42
