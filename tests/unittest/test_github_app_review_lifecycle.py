import asyncio
from collections import deque
from types import SimpleNamespace

from pr_agent.servers import github_app


API_URL = "https://api.github.com/repos/owner/repo/pulls/7"
HEAD_SHA = "abc123"


class _Settings:
    def __init__(self, commands, publish_lifecycle=True, timeout_seconds=1, push_commands=None):
        self.commands = commands
        self.push_commands = push_commands or []
        self.config = SimpleNamespace(disable_auto_feedback=False)
        self.github = SimpleNamespace(
            publish_review_lifecycle=publish_lifecycle,
            review_lifecycle_timeout_seconds=timeout_seconds,
        )
        self.is_auto_command = False

    def get(self, key):
        if key == "github_app.pr_commands":
            return self.commands
        if key == "github_app.push_commands":
            return self.push_commands
        raise AssertionError(f"Unexpected settings key: {key}")

    def set(self, key, value):
        assert key == "config.is_auto_command"
        self.is_auto_command = value


class _Requester:
    def __init__(self):
        self.requests = []

    def requestJsonAndCheck(self, method, url, input=None):
        self.requests.append((method, url, input))
        if method == "POST":
            return {}, {"id": 42}
        return {}, {}


class _Agent:
    def __init__(self, outcomes):
        self.outcomes = deque(outcomes)
        self.commands = []

    async def handle_request(self, api_url, command, publish_review_decision=False):
        self.commands.append((api_url, command, publish_review_decision))
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return await outcome()
        return outcome


def _body(head_sha=HEAD_SHA):
    return {"pull_request": {"head": {"sha": head_sha}}}


def _install_environment(monkeypatch, settings):
    requester = _Requester()
    provider = SimpleNamespace(
        base_url="https://api.github.com",
        repo="owner/repo",
        pr=SimpleNamespace(_requester=requester),
    )
    provider_urls = []

    def get_provider(api_url):
        provider_urls.append(api_url)
        return provider

    monkeypatch.setattr(github_app, "get_settings", lambda: settings)
    monkeypatch.setattr(github_app, "apply_repo_settings", lambda api_url: None)
    monkeypatch.setattr(github_app, "should_process_pr_logic", lambda body: True)
    monkeypatch.setattr(github_app, "get_git_provider_with_context", get_provider)
    return requester, provider_urls


def _run(agent):
    return asyncio.run(
        github_app._perform_auto_commands_github(
            "pr_commands", agent, _body(), API_URL, {}
        )
    )


def test_automatic_commands_publish_success_for_the_webhook_head(monkeypatch):
    settings = _Settings(["/review", "/improve"])
    requester, provider_urls = _install_environment(monkeypatch, settings)
    agent = _Agent([True, True])

    result = _run(agent)

    assert result is True
    assert provider_urls == [API_URL]
    assert agent.commands == [
        (API_URL, "/review", True),
        (API_URL, "/improve", False),
    ]
    assert requester.requests == [
        (
            "POST",
            "https://api.github.com/repos/owner/repo/check-runs",
            {"name": "PR-Agent Review", "head_sha": HEAD_SHA, "status": "queued"},
        ),
        (
            "PATCH",
            "https://api.github.com/repos/owner/repo/check-runs/42",
            {"status": "in_progress"},
        ),
        (
            "PATCH",
            "https://api.github.com/repos/owner/repo/check-runs/42",
            {"status": "completed", "conclusion": "success"},
        ),
    ]


def test_false_command_result_publishes_failure_after_all_commands(monkeypatch):
    settings = _Settings(["/review", "/improve"])
    requester, _ = _install_environment(monkeypatch, settings)
    agent = _Agent([True, False])

    result = _run(agent)

    assert result is False
    assert agent.commands == [
        (API_URL, "/review", True),
        (API_URL, "/improve", False),
    ]
    assert requester.requests[-1][2]["conclusion"] == "failure"


def test_command_exception_publishes_failure(monkeypatch):
    settings = _Settings(["/review", "/improve"])
    requester, _ = _install_environment(monkeypatch, settings)
    agent = _Agent([RuntimeError("command failed"), True])

    result = _run(agent)

    assert result is False
    assert agent.commands == [(API_URL, "/review", True)]
    assert requester.requests[-1][2]["conclusion"] == "failure"


def test_command_timeout_publishes_failure(monkeypatch):
    settings = _Settings(["/review"], timeout_seconds=0.001)
    requester, _ = _install_environment(monkeypatch, settings)

    async def slow_command():
        await asyncio.sleep(0.02)
        return True

    result = _run(_Agent([slow_command]))

    assert result is False
    assert requester.requests[-1][2]["conclusion"] == "failure"


def test_disabled_lifecycle_preserves_command_order_and_returns_aggregate(monkeypatch):
    settings = _Settings(["/review", "/improve"], publish_lifecycle=False)
    agent = _Agent([False, True])
    monkeypatch.setattr(github_app, "get_settings", lambda: settings)
    monkeypatch.setattr(github_app, "apply_repo_settings", lambda api_url: None)
    monkeypatch.setattr(github_app, "should_process_pr_logic", lambda body: True)
    monkeypatch.setattr(
        github_app,
        "get_git_provider_with_context",
        lambda api_url: (_ for _ in ()).throw(AssertionError("lifecycle created")),
    )

    result = _run(agent)

    assert result is False
    assert agent.commands == [
        (API_URL, "/review", True),
        (API_URL, "/improve", False),
    ]


def test_review_pr_alias_enables_decision_publication(monkeypatch):
    settings = _Settings(["/review_pr"])
    _requester, _provider_urls = _install_environment(monkeypatch, settings)
    agent = _Agent([True])

    result = _run(agent)

    assert result is True
    assert agent.commands == [(API_URL, "/review_pr", True)]


def test_decision_write_failure_concludes_the_automatic_lifecycle(monkeypatch):
    settings = _Settings(["/review"])
    requester, _provider_urls = _install_environment(monkeypatch, settings)
    agent = _Agent([False])

    result = _run(agent)

    assert result is False
    assert agent.commands == [(API_URL, "/review", True)]
    assert requester.requests[-1][2]["conclusion"] == "failure"


def test_synchronize_lifecycle_uses_the_new_webhook_head(monkeypatch):
    new_head = "new-head"
    settings = _Settings([], push_commands=["/review"])
    requester, _provider_urls = _install_environment(monkeypatch, settings)
    agent = _Agent([True])

    result = asyncio.run(
        github_app._perform_auto_commands_github(
            "push_commands", agent, _body(new_head), API_URL, {}
        )
    )

    assert result is True
    assert agent.commands == [(API_URL, "/review", True)]
    assert requester.requests[0][2]["head_sha"] == new_head
