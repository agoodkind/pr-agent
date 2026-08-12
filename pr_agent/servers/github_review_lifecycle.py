CHECK_RUN_NAME = "PR-Agent Review"


class ReviewLifecycle:
    def __init__(self, git_provider, head_sha):
        self._base_url = git_provider.base_url
        self._repository = git_provider.repo
        self._requester = git_provider.pr._requester
        self._head_sha = head_sha
        self._check_run_id = None

    @property
    def check_run_id(self):
        return self._check_run_id

    def create_queued(self):
        _, response = self._requester.requestJsonAndCheck(
            "POST",
            f"{self._base_url}/repos/{self._repository}/check-runs",
            input={
                "name": CHECK_RUN_NAME,
                "head_sha": self._head_sha,
                "status": "queued",
            },
        )
        self._check_run_id = response["id"]

    def start(self):
        self._update({"status": "in_progress"})

    def succeed(self):
        self._update({"status": "completed", "conclusion": "success"})

    def fail(self, reason):
        self._update(
            {
                "status": "completed",
                "conclusion": "failure",
                "output": {"title": CHECK_RUN_NAME, "summary": reason},
            }
        )

    def _update(self, body):
        if self._check_run_id is None:
            raise RuntimeError("Review lifecycle check run has not been created")
        self._requester.requestJsonAndCheck(
            "PATCH",
            f"{self._base_url}/repos/{self._repository}/check-runs/"
            f"{self._check_run_id}",
            input=body,
        )
