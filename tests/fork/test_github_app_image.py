from __future__ import annotations

import json
import subprocess
import time
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import urlopen

import pytest


IMAGE_NAME = "pr-agent-fork-check"
STARTUP_TIMEOUT_SECONDS = 60


def docker_output(*args: str) -> str:
    completed_process = subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed_process.stdout.strip()


def test_github_app_image_serves_root_as_nonroot() -> None:
    docker_output(
        "build",
        "--no-cache",
        "--target",
        "github_app",
        "--tag",
        IMAGE_NAME,
        "-f",
        "docker/Dockerfile",
        ".",
    )
    docker_output(
        "run",
        "--rm",
        "--user",
        "65534:65534",
        "--env",
        "HOME=/tmp",
        "--env",
        "CONFIG__LOG_LEVEL=WARNING",
        "--entrypoint",
        "python",
        IMAGE_NAME,
        "-c",
        "from pr_agent.servers.github_app import app",
    )
    container_id = docker_output(
        "run",
        "--detach",
        "--user",
        "65534:65534",
        "--env",
        "HOME=/tmp",
        "--env",
        "CONFIG__LOG_LEVEL=WARNING",
        "--publish",
        "127.0.0.1::3000",
        IMAGE_NAME,
    )

    try:
        port = docker_output("port", container_id, "3000/tcp").rsplit(":", maxsplit=1)[1]
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        last_error: HTTPException | URLError | None = None
        while time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    assert response.status == 200
                    assert json.load(response) == {"status": "ok"}
                    return
            except (HTTPException, URLError) as error:
                last_error = error
                time.sleep(1)

        pytest.fail(f"GitHub App image did not serve / within {STARTUP_TIMEOUT_SECONDS} seconds: {last_error}")
    finally:
        subprocess.run(["docker", "rm", "--force", container_id], check=False, capture_output=True)
