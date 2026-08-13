from __future__ import annotations

import pytest

from pr_agent.algo.review_decision import (
    ReviewEvent,
    ReviewFinding,
    assess_review,
)


def finding(importance: int | None) -> ReviewFinding:
    return ReviewFinding(
        relevant_file="src/example.py",
        issue_header="Possible bug",
        issue_content="A realistic input triggers the issue.",
        start_line=10,
        end_line=12,
        importance=importance,
    )


@pytest.mark.parametrize(
    ("findings", "remaining_files", "expected_event"),
    [
        ([], [], ReviewEvent.APPROVE),
        ([finding(importance=7)], [], ReviewEvent.REQUEST_CHANGES),
        ([finding(importance=6)], [], ReviewEvent.COMMENT),
        ([], ["large.py"], ReviewEvent.COMMENT),
        ([finding(importance=None)], [], ReviewEvent.COMMENT),
        ([finding(importance=10)], ["large.py"], ReviewEvent.REQUEST_CHANGES),
        ([finding(importance=11)], [], ReviewEvent.COMMENT),
    ],
)
def test_assess_review_selects_safe_event(
    findings: list[ReviewFinding],
    remaining_files: list[str],
    expected_event: ReviewEvent,
) -> None:
    assessment = assess_review(findings, remaining_files, minimum_importance=7)

    assert assessment.event is expected_event
    assert assessment.findings == findings
    assert assessment.full_coverage is (not remaining_files)


def test_assess_review_comments_when_finding_omits_importance() -> None:
    finding_without_importance = ReviewFinding.model_construct(
        relevant_file="src/example.py",
        issue_header="Possible bug",
        issue_content="A realistic input triggers the issue.",
        start_line=10,
        end_line=12,
    )

    assessment = assess_review([finding_without_importance], [], minimum_importance=7)

    assert assessment.event is ReviewEvent.COMMENT


def test_assess_review_comments_when_finding_importance_is_not_an_integer() -> None:
    finding_with_text_importance = ReviewFinding.model_construct(
        relevant_file="src/example.py",
        issue_header="Possible bug",
        issue_content="A realistic input triggers the issue.",
        start_line=10,
        end_line=12,
        importance="7",
    )

    assessment = assess_review([finding_with_text_importance], [], minimum_importance=7)

    assert assessment.event is ReviewEvent.COMMENT
