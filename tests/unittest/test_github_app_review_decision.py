from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from pr_agent.identity_providers.identity_provider import Eligibility
from pr_agent.servers import github_app


API_URL = "https://api.github.com/repos/owner/repo/pulls/7"


class RecordingAgent:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bool]] = []

    async def handle_request(
        self,
        api_url: str,
        command: str,
        notify=None,
        publish_review_decision: bool = False,
    ) -> bool:
        self.requests.append((api_url, command, publish_review_decision))
        if notify is not None:
            notify()
        return True


class RecordingProvider:
    def __init__(self, authorized_users: set[str]) -> None:
        self.authorized_users = authorized_users
        self.reactions: list[tuple[int, bool]] = []

    def can_publish_review_decision(self, sender: str) -> bool:
        return sender in self.authorized_users

    def add_eyes_reaction(self, comment_id: int, disable_eyes: bool = False) -> None:
        self.reactions.append((comment_id, disable_eyes))


class EligibleIdentityProvider:
    def verify_eligibility(
        self, provider: str, sender_id: str, api_url: str
    ) -> Eligibility:
        return Eligibility.ELIGIBLE


def webhook_body(command: str) -> dict[str, object]:
    return {
        "comment": {
            "body": command,
            "id": 31,
            "pull_request_url": API_URL,
        }
    }


def install_handler_dependencies(monkeypatch, provider: RecordingProvider) -> None:
    monkeypatch.setattr(github_app, "get_git_provider_with_context", lambda pr_url: provider)
    monkeypatch.setattr(github_app, "get_identity_provider", EligibleIdentityProvider)


@pytest.mark.parametrize(
    ("sender", "authorized_users", "expected"),
    [
        ("pull-author", {"pull-author"}, True),
        ("repository-collaborator", {"repository-collaborator"}, True),
        ("other-eligible-commenter", set(), False),
    ],
)
def test_manual_review_passes_decision_only_for_authorized_actors(
    monkeypatch, sender: str, authorized_users: set[str], expected: bool
) -> None:
    provider = RecordingProvider(authorized_users)
    install_handler_dependencies(monkeypatch, provider)
    agent = RecordingAgent()

    asyncio.run(
        github_app.handle_comments_on_pr(
            webhook_body("/review"),
            "issue_comment",
            sender,
            "123",
            "created",
            {},
            agent,
        )
    )

    assert agent.requests == [(API_URL, "/review", expected)]
    assert provider.reactions == [(31, False)]


def test_manual_improve_never_passes_decision_publication(monkeypatch) -> None:
    provider = RecordingProvider({"pull-author"})
    install_handler_dependencies(monkeypatch, provider)
    agent = RecordingAgent()

    asyncio.run(
        github_app.handle_comments_on_pr(
            webhook_body("/improve"),
            "issue_comment",
            "pull-author",
            "123",
            "created",
            {},
            agent,
        )
    )

    assert agent.requests == [(API_URL, "/improve", False)]
