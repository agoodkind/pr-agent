import json
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from pr_agent.servers.github_review_lifecycle import ReviewLifecycle


class _GitHubFixture:
    def __init__(self):
        self.requests = []
        self.responses = deque()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def _handle_request(self):
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                fixture.requests.append(
                    (self.command, self.path, json.loads(body) if body else None)
                )
                response = json.dumps(fixture.responses.popleft()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            do_PATCH = _handle_request
            do_POST = _handle_request

            def log_message(self, format, *args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = Thread(target=self._server.serve_forever)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._server.shutdown()
        self._thread.join()
        self._server.server_close()


class _FixtureRequester:
    def requestJsonAndCheck(self, method, url, input=None):
        payload = json.dumps(input).encode("utf-8") if input is not None else None
        request = Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urlopen(request) as response:
            return {}, json.loads(response.read())


def _lifecycle(fixture):
    provider = SimpleNamespace(
        base_url=fixture.base_url,
        repo="owner/repo",
        pr=SimpleNamespace(_requester=_FixtureRequester()),
    )
    return ReviewLifecycle(provider, head_sha="abc123")


def test_lifecycle_publishes_queued_running_and_success():
    with _GitHubFixture() as fixture:
        fixture.responses.extend([{"id": 42}, {}, {}])
        lifecycle = _lifecycle(fixture)

        lifecycle.create_queued()
        lifecycle.start()
        lifecycle.succeed()

    assert fixture.requests == [
        (
            "POST",
            "/repos/owner/repo/check-runs",
            {
                "name": "PR-Agent Review",
                "head_sha": "abc123",
                "status": "queued",
            },
        ),
        ("PATCH", "/repos/owner/repo/check-runs/42", {"status": "in_progress"}),
        (
            "PATCH",
            "/repos/owner/repo/check-runs/42",
            {"status": "completed", "conclusion": "success"},
        ),
    ]


def test_lifecycle_publishes_failure_reason():
    with _GitHubFixture() as fixture:
        fixture.responses.extend([{"id": 42}, {}])
        lifecycle = _lifecycle(fixture)

        lifecycle.create_queued()
        lifecycle.fail("review command failed")

    assert fixture.requests[-1] == (
        "PATCH",
        "/repos/owner/repo/check-runs/42",
        {
            "status": "completed",
            "conclusion": "failure",
            "output": {
                "title": "PR-Agent Review",
                "summary": "review command failed",
            },
        },
    )


def test_lifecycle_requires_a_created_check_before_updates():
    with _GitHubFixture() as fixture:
        lifecycle = _lifecycle(fixture)

        with pytest.raises(RuntimeError, match="has not been created"):
            lifecycle.start()
