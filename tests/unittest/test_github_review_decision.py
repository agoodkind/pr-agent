from __future__ import annotations

from types import SimpleNamespace

import pytest

from pr_agent.algo.review_decision import (
    ReviewAssessment,
    ReviewEvent,
    ReviewFinding,
)
from pr_agent.algo.types import FilePatchInfo
from pr_agent.git_providers.github_provider import GithubProvider


class FakeRepository:
    def __init__(self, collaborators: dict[str, str] | None = None) -> None:
        self.collaborators = collaborators or {}

    def get_commit(self, sha: str) -> SimpleNamespace:
        return SimpleNamespace(sha=sha)

    def get_collaborator_permission(self, login: str) -> str:
        if login not in self.collaborators:
            raise KeyError(login)
        return self.collaborators[login]


class FakePullRequest:
    def __init__(
        self,
        head_sha: str,
        review_bodies: list[str] | None = None,
        reviewer_login: str = "pr-agent[bot]",
    ) -> None:
        self.head = SimpleNamespace(sha=head_sha)
        self.user = SimpleNamespace(login="pull-author")
        self.create_review_calls: list[dict[str, object]] = []
        self._reviews = [
            SimpleNamespace(body=body, user=SimpleNamespace(login=reviewer_login))
            for body in review_bodies or []
        ]

    def create_review(self, **kwargs: object) -> None:
        self.create_review_calls.append(kwargs)

    def get_reviews(self) -> list[SimpleNamespace]:
        return self._reviews


def make_finding(
    relevant_file: str,
    start_line: int,
    end_line: int,
    importance: int | None = 8,
) -> ReviewFinding:
    return ReviewFinding(
        relevant_file=relevant_file,
        issue_header="Incorrect result",
        issue_content="The endpoint returns the wrong result for this input.",
        start_line=start_line,
        end_line=end_line,
        importance=importance,
    )


def make_assessment(
    findings: list[ReviewFinding],
    body: str = "Review guide includes every finding.",
) -> ReviewAssessment:
    return ReviewAssessment(
        event=ReviewEvent.REQUEST_CHANGES,
        body=body,
        findings=findings,
        full_coverage=True,
        has_invalid_findings=False,
    )


def make_provider(
    head_sha: str = "analyzed-head",
    review_bodies: list[str] | None = None,
    reviewer_login: str = "pr-agent[bot]",
    collaborators: dict[str, str] | None = None,
) -> tuple[GithubProvider, FakePullRequest]:
    pull_request = FakePullRequest(head_sha, review_bodies, reviewer_login)
    provider = GithubProvider.__new__(GithubProvider)
    provider.pr = pull_request
    provider.repo = "owner/repo"
    provider.github_user_id = "pr-agent[bot]"
    provider.max_comment_chars = 65000
    provider._get_pr = lambda: pull_request
    provider._get_repo = lambda: FakeRepository(collaborators)
    provider.get_diff_files = lambda: [
        FilePatchInfo(
            base_file="",
            head_file="",
            patch="@@ -0,0 +1,4 @@\n+first\n+second\n+third\n+fourth\n",
            filename="src/changed.py",
        )
    ]
    return provider, pull_request


def test_publish_review_decision_posts_one_review_with_right_side_comments() -> None:
    provider, pull_request = make_provider()
    assessment = make_assessment(
        [
            make_finding("src/changed.py", 1, 1),
            make_finding("src/changed.py", 2, 3),
        ]
    )

    provider.publish_review_decision(assessment, "analyzed-head")

    assert pull_request.create_review_calls == [
        {
            "commit": SimpleNamespace(sha="analyzed-head"),
            "event": "REQUEST_CHANGES",
            "body": (
                "Review guide includes every finding.\n\n"
                "<!-- pr-agent-review-decision:sha=analyzed-head;policy=1 -->"
            ),
            "comments": [
                {
                    "body": (
                        "**Incorrect result**\n\n"
                        "The endpoint returns the wrong result for this input.\n\n"
                        "Importance: 8"
                    ),
                    "path": "src/changed.py",
                    "line": 1,
                    "side": "RIGHT",
                },
                {
                    "body": (
                        "**Incorrect result**\n\n"
                        "The endpoint returns the wrong result for this input.\n\n"
                        "Importance: 8"
                    ),
                    "path": "src/changed.py",
                    "start_line": 2,
                    "start_side": "RIGHT",
                    "line": 3,
                    "side": "RIGHT",
                },
            ],
        }
    ]


def test_publish_review_decision_anchors_a_contextual_finding_to_a_changed_line() -> None:
    provider, pull_request = make_provider()
    assessment = make_assessment([make_finding("src/changed.py", 2, 5)])

    provider.publish_review_decision(assessment, "analyzed-head")

    assert pull_request.create_review_calls[0]["comments"] == [
        {
            "body": (
                "**Incorrect result**\n\n"
                "The endpoint returns the wrong result for this input.\n\n"
                "Importance: 8"
            ),
            "path": "src/changed.py",
            "line": 4,
            "side": "RIGHT",
        }
    ]


def test_publish_review_decision_normalizes_inline_finding_paths() -> None:
    provider, pull_request = make_provider()
    assessment = make_assessment([make_finding(" src/changed.py\n", 1, 1)])

    provider.publish_review_decision(assessment, "analyzed-head")

    assert pull_request.create_review_calls[0]["comments"] == [
        {
            "body": (
                "**Incorrect result**\n\n"
                "The endpoint returns the wrong result for this input.\n\n"
                "Importance: 8"
            ),
            "path": "src/changed.py",
            "line": 1,
            "side": "RIGHT",
        }
    ]


def test_publish_review_decision_keeps_invalid_inline_findings_in_body() -> None:
    provider, pull_request = make_provider()
    assessment = make_assessment(
        [
            make_finding("src/missing.py", 1, 1),
            make_finding("src/changed.py", 9, 9),
        ],
        body="Guide contains src/missing.py and src/changed.py findings.",
    )

    provider.publish_review_decision(assessment, "analyzed-head")

    assert pull_request.create_review_calls == [
        {
            "commit": SimpleNamespace(sha="analyzed-head"),
            "event": "REQUEST_CHANGES",
            "body": (
                "Guide contains src/missing.py and src/changed.py findings.\n\n"
                "<!-- pr-agent-review-decision:sha=analyzed-head;policy=1 -->"
            ),
            "comments": [],
        }
    ]


def test_publish_review_decision_rejects_a_stale_head_before_creating_review() -> None:
    provider, pull_request = make_provider(head_sha="current-head")

    with pytest.raises(ValueError, match="head changed"):
        provider.publish_review_decision(make_assessment([]), "analyzed-head")

    assert pull_request.create_review_calls == []


@pytest.mark.parametrize(
    ("sender_login", "collaborators", "expected"),
    [
        ("pull-author", {}, True),
        ("write-collaborator", {"write-collaborator": "write"}, True),
        ("admin-collaborator", {"admin-collaborator": "admin"}, True),
        ("read-collaborator", {"read-collaborator": "read"}, False),
        ("other-user", {}, False),
    ],
)
def test_can_publish_review_decision_requires_author_or_collaborator(
    sender_login: str,
    collaborators: set[str],
    expected: bool,
) -> None:
    provider, _ = make_provider(collaborators=collaborators)

    assert provider.can_publish_review_decision(sender_login) is expected


def test_publish_review_decision_skips_an_exact_same_head_marker() -> None:
    provider, pull_request = make_provider(
        review_bodies=[
            "Guide\n\n<!-- pr-agent-review-decision:sha=analyzed-head;policy=1 -->"
        ]
    )

    provider.publish_review_decision(make_assessment([]), "analyzed-head")

    assert pull_request.create_review_calls == []


def test_publish_review_decision_ignores_same_head_marker_from_other_user() -> None:
    provider, pull_request = make_provider(
        review_bodies=[
            "Guide\n\n<!-- pr-agent-review-decision:sha=analyzed-head;policy=1 -->"
        ],
        reviewer_login="other-user",
    )

    provider.publish_review_decision(make_assessment([]), "analyzed-head")

    assert len(pull_request.create_review_calls) == 1


def test_publish_review_decision_posts_for_a_new_head_despite_old_marker() -> None:
    provider, pull_request = make_provider(
        head_sha="new-head",
        review_bodies=[
            "Guide\n\n<!-- pr-agent-review-decision:sha=old-head;policy=1 -->"
        ],
    )

    provider.publish_review_decision(make_assessment([]), "new-head")

    assert len(pull_request.create_review_calls) == 1
    assert pull_request.create_review_calls[0]["commit"] == SimpleNamespace(sha="new-head")
