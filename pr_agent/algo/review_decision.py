from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, field_validator


class ReviewEvent(StrEnum):
    APPROVE = "APPROVE"
    COMMENT = "COMMENT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class ReviewFinding(BaseModel):
    relevant_file: str
    issue_header: str
    issue_content: str
    start_line: int
    end_line: int
    importance: int | None

    @field_validator("importance", mode="before")
    @classmethod
    def require_integer_importance(cls, value: int | None) -> int | None:
        if value is None or type(value) is int:
            return value
        raise ValueError("importance must be an integer")


class ReviewAssessment(BaseModel):
    event: ReviewEvent
    body: str
    findings: list[ReviewFinding]
    full_coverage: bool


def assess_review(
    findings: list[ReviewFinding],
    remaining_files: list[str],
    minimum_importance: int,
) -> ReviewAssessment:
    full_coverage = not remaining_files
    has_invalid_importance = False

    for finding in findings:
        importance = getattr(finding, "importance", None)
        if type(importance) is not int or not 1 <= importance <= 10:
            has_invalid_importance = True
            continue
        if importance >= minimum_importance:
            return ReviewAssessment(
                event=ReviewEvent.REQUEST_CHANGES,
                body="",
                findings=findings,
                full_coverage=full_coverage,
            )

    if findings or not full_coverage or has_invalid_importance:
        return ReviewAssessment(
            event=ReviewEvent.COMMENT,
            body="",
            findings=findings,
            full_coverage=full_coverage,
        )

    return ReviewAssessment(
        event=ReviewEvent.APPROVE,
        body="",
        findings=findings,
        full_coverage=True,
    )
