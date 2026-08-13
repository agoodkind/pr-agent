# GitHub Review Decisions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each enabled `/review` publish its existing guide, key findings as inline comments, and one GitHub review decision for the analyzed pull request head.

**Architecture:** Extend the existing structured review response with an optional importance score. A pure policy converts that response into `APPROVE`, `COMMENT`, or `REQUEST_CHANGES`. The GitHub provider validates inline locations and submits the decision and valid inline comments as one review. The GitHub App passes an explicit per-request authorization flag, while the existing lifecycle check reports publication failures.

**Tech Stack:** Python 3.12, Pydantic 2, PyGithub, FastAPI, pytest, GitHub Actions, Docker, JavaScript, Cloudflare Containers

The [approved design](../specs/2026-08-12-github-review-decisions-design.md) defines the product behavior. This plan implements that design without changing `/improve` source behavior or adding another model request.

## Global Constraints

- Keep `github.publish_review_decision = false` as the upstream default.
- Preserve the existing persistent review guide for every `/review`.
- Add importance only when decision publication is enabled for the current request.
- Never parse published Markdown to calculate a decision.
- Never approve after incomplete coverage, invalid data, stale-head detection, or publication failure.
- Submit the decision and all valid inline comments in one GitHub review against the analyzed head.
- Keep invalid inline findings in the persistent guide and in decision policy inputs.
- Run only `/review` automatically in the Cloudflare consumer. Keep `/improve` available manually.
- Use signed commits with `Co-authored-by: Codex <noreply@openai.com>`.
- Fetch before every branch comparison. Compare against remote-tracking refs.

---

## Task 1: Add the pure review decision policy

**Files:**

- Create: `pr_agent/algo/review_decision.py`
- Modify: `pr_agent/settings/configuration.toml`
- Modify: `pr_agent/settings/pr_reviewer_prompts.toml`
- Modify: `pr_agent/tools/pr_reviewer.py`
- Create: `tests/unittest/test_review_decision.py`
- Modify: `tests/unittest/test_pr_reviewer_core.py`

- [ ] **Step 1: Add failing policy tests**

Create table-driven tests through the public `assess_review` function. Cover the exact precedence:

```python
@pytest.mark.parametrize(
    ("findings", "remaining_files", "expected_event"),
    [
        ([], [], ReviewEvent.APPROVE),
        ([finding(importance=7)], [], ReviewEvent.REQUEST_CHANGES),
        ([finding(importance=6)], [], ReviewEvent.COMMENT),
        ([], ["large.py"], ReviewEvent.COMMENT),
        ([finding(importance=None)], [], ReviewEvent.COMMENT),
        ([finding(importance=10)], ["large.py"], ReviewEvent.REQUEST_CHANGES),
    ],
)
def test_assess_review_selects_safe_event(
    findings: list[ReviewFinding],
    remaining_files: list[str],
    expected_event: ReviewEvent,
) -> None:
    assessment = assess_review(findings, remaining_files, minimum_importance=7)

    assert assessment.event is expected_event
```

Run:

```bash
uv run pytest tests/unittest/test_review_decision.py -q
```

Expected: fail because `pr_agent.algo.review_decision` does not exist.

- [ ] **Step 2: Implement typed assessment models and policy**

Define:

```python
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


class ReviewAssessment(BaseModel):
    event: ReviewEvent
    body: str
    findings: list[ReviewFinding]
    full_coverage: bool
```

Implement `assess_review` as a pure function. Treat missing, non-integer, and out-of-range importance as invalid input that forces `COMMENT`. Apply this order:

1. Return `REQUEST_CHANGES` when any valid importance meets the threshold.
2. Return `COMMENT` when findings exist, coverage is incomplete, or any importance is invalid.
3. Return `APPROVE` only when coverage is complete and findings are empty.

Include the analyzed head separately at publication time. Keep GitHub and provider objects out of this module.

- [ ] **Step 3: Add opt-in configuration and conditional prompt fields**

Add these defaults under `[github]`:

```toml
publish_review_decision = false
review_decision_min_importance = 7
```

Add `importance: int` to `KeyIssuesComponentLink` only when a new prompt variable named `publish_review_decision` is true. Update the prompt example under the same condition.

Pass `publish_review_decision` into `PRReviewer` explicitly for the current request. Do not derive per-request authorization from mutable global settings.

- [ ] **Step 4: Preserve structured data before Markdown rendering**

Change `_prepare_pr_review` to retain the parsed review data before `convert_to_markdown_v2` mutates it. Parse findings through `ReviewFinding` and calculate one `ReviewAssessment` from:

- the structured key findings;
- `self.remaining_files_list`;
- `github.review_decision_min_importance`.

When decision publication is disabled for the request, preserve the current prompt and output path exactly.

- [ ] **Step 5: Prove prompt and disabled-mode behavior**

Add behavior tests that render the real reviewer prompt:

- enabled requests include `importance` in the key-finding schema;
- disabled requests omit `importance`;
- the disabled path publishes the same guide and makes no decision assessment available.

Run:

```bash
uv run pytest tests/unittest/test_review_decision.py tests/unittest/test_pr_reviewer_core.py -q
```

Expected: pass.

- [ ] **Step 6: Commit the policy slice**

```bash
git add pr_agent/algo/review_decision.py pr_agent/settings/configuration.toml pr_agent/settings/pr_reviewer_prompts.toml pr_agent/tools/pr_reviewer.py tests/unittest/test_review_decision.py tests/unittest/test_pr_reviewer_core.py
git commit -S -m "Add GitHub review decision policy" -m "Co-authored-by: Codex <noreply@openai.com>"
```

---

## Task 2: Publish one GitHub review with inline findings

**Files:**

- Modify: `pr_agent/git_providers/github_provider.py`
- Modify: `tests/unittest/test_github_provider_comments.py`
- Create: `tests/unittest/test_github_review_decision.py`

- [ ] **Step 1: Add failing publication tests**

Exercise the provider boundary with a narrow fake pull request. Assert that one call to `create_review` receives:

```python
{
    "commit": analyzed_commit,
    "event": "REQUEST_CHANGES",
    "body": expected_body,
    "comments": expected_inline_comments,
}
```

Cover:

- one-line and multi-line RIGHT-side comments;
- invalid paths and lines omitted from `comments`;
- omitted inline comments still present in the assessment body;
- a stale current head raises before `create_review`;
- the pull request author is authorized;
- an explicit repository collaborator is authorized;
- another user is not authorized.

Run:

```bash
uv run pytest tests/unittest/test_github_review_decision.py -q
```

Expected: fail because the provider has no review-decision publisher.

- [ ] **Step 2: Add explicit authorization**

Implement:

```python
def can_publish_review_decision(self, sender_login: str) -> bool:
```

Return true when `sender_login` equals `self.pr.user.login`. Otherwise use the repository collaborator API. Do not use general read access as collaborator proof.

- [ ] **Step 3: Validate inline locations without creating pending reviews**

Implement a pure conversion boundary that accepts `Sequence[ReviewFinding]` and returns GitHub review comment payloads. Validate each finding against the current diff's RIGHT-side changed lines before publication.

Do not reuse `_verify_code_comments`, because it creates and deletes pending reviews. Reuse its diff parsing logic or extract that logic into a side-effect-free helper.

Use these payload shapes:

```python
{
    "body": body,
    "path": finding.relevant_file,
    "line": finding.end_line,
    "side": "RIGHT",
}
```

```python
{
    "body": body,
    "path": finding.relevant_file,
    "start_line": finding.start_line,
    "start_side": "RIGHT",
    "line": finding.end_line,
    "side": "RIGHT",
}
```

Include the importance score in the inline body only when it is valid.

- [ ] **Step 4: Submit the decision atomically for one head**

Implement:

```python
def publish_review_decision(
    self,
    assessment: ReviewAssessment,
    analyzed_head_sha: str,
) -> None:
```

Refresh the pull request head and compare it with `analyzed_head_sha`. Raise on mismatch. Resolve the exact analyzed commit and call `self.pr.create_review` once with the event, body, and every valid inline comment.

Keep the existing persistent guide publication separate and earlier. A rejected inline location must not remove the finding from the decision calculation.

- [ ] **Step 5: Prevent same-head duplicate automated reviews**

Use a stable hidden marker in the review body containing the analyzed head SHA and policy version. Before creating an automatic decision, inspect existing reviews for that exact marker. Reuse or skip only an exact same-head match. A new head must receive a new review.

Add tests for same-head retry and new-head publication.

- [ ] **Step 6: Run focused provider tests**

```bash
uv run pytest tests/unittest/test_github_provider_comments.py tests/unittest/test_github_review_decision.py -q
```

Expected: pass.

- [ ] **Step 7: Commit the provider slice**

```bash
git add pr_agent/git_providers/github_provider.py tests/unittest/test_github_provider_comments.py tests/unittest/test_github_review_decision.py
git commit -S -m "Publish GitHub review decisions" -m "Co-authored-by: Codex <noreply@openai.com>"
```

---

## Task 3: Connect manual and automatic review runs

**Files:**

- Modify: `pr_agent/agent/pr_agent.py`
- Modify: `pr_agent/tools/pr_reviewer.py`
- Modify: `pr_agent/servers/github_app.py`
- Modify: `tests/unittest/test_github_app_review_lifecycle.py`
- Create: `tests/unittest/test_github_app_review_decision.py`

- [ ] **Step 1: Add failing webhook behavior tests**

Drive the real GitHub App handler with recorded webhook bodies and narrow provider fakes. Cover:

- automatic `/review` enables decision publication;
- automatic `/review_pr` alias enables decision publication;
- manual `/review` by the pull request author enables publication;
- manual `/review` by an explicit collaborator enables publication;
- manual `/review` by another eligible commenter keeps normal comments but disables the decision;
- manual `/improve` never enables the decision;
- review findings still allow lifecycle success after publication;
- a decision write exception makes the automatic command return false and concludes lifecycle failure;
- a new `synchronize` event uses the new head.

Run:

```bash
uv run pytest tests/unittest/test_github_app_review_decision.py tests/unittest/test_github_app_review_lifecycle.py -q
```

Expected: fail because the request path does not carry decision context.

- [ ] **Step 2: Pass decision capability through explicit call arguments**

Add a typed per-request flag or context to:

```python
PRAgent.handle_request(..., publish_review_decision=False)
PRAgent._handle_request(..., publish_review_decision=False)
PRReviewer(..., publish_review_decision=False)
```

Do not write per-request authorization into the global settings object.

In `_perform_auto_commands_github`, enable the flag only for `/review` and its accepted aliases. In `handle_comments_on_pr`, enable it only for an authorized manual `/review` actor.

- [ ] **Step 3: Publish in the required order**

In `PRReviewer.run`:

1. analyze the exact head;
2. publish the existing persistent guide;
3. publish the GitHub decision when the request flag and configuration flag are both true.

Add an event-log assertion proving the guide publication precedes `create_review`.

- [ ] **Step 4: Propagate only required publication failures**

When enabled decision publication fails, re-raise that failure through `PRAgent.handle_request` so the automatic command returns false. Do not turn on broad error propagation for unrelated tools.

The lifecycle check must remain successful when the reviewer finds defects and successfully publishes `REQUEST_CHANGES`. It must fail only when analysis or required output publication fails.

- [ ] **Step 5: Run the focused orchestration suite**

```bash
uv run pytest tests/unittest/test_github_app_review_decision.py tests/unittest/test_github_app_review_lifecycle.py tests/unittest/test_pr_reviewer_core.py -q
```

Expected: pass.

- [ ] **Step 6: Run the full fork validation locally**

```bash
uv run pytest tests/unittest -q
docker build --target github_app -t pr-agent-review-decisions:test .
uv run pytest tests/fork/test_github_app_image.py -q
```

Expected: all commands pass. The image test must start the exact `github_app` target as the non-root runtime user and receive the health response.

- [ ] **Step 7: Commit the orchestration slice**

```bash
git add pr_agent/agent/pr_agent.py pr_agent/tools/pr_reviewer.py pr_agent/servers/github_app.py tests/unittest/test_github_app_review_lifecycle.py tests/unittest/test_github_app_review_decision.py
git commit -S -m "Connect GitHub review decision publishing" -m "Co-authored-by: Codex <noreply@openai.com>"
```

---

## Task 4: Land and protect the fork patch

**Files:**

- Source repository history and GitHub repository settings only

- [ ] **Step 1: Verify the complete source branch**

```bash
git fetch fork
git diff --check fork/main...HEAD
uv run pytest tests/unittest -q
docker build --target github_app -t pr-agent-review-decisions:test .
uv run pytest tests/fork/test_github_app_image.py -q
git log --show-signature --oneline fork/main..HEAD
```

Expected: clean diff, green tests, successful image validation, and valid signatures on every branch commit.

- [ ] **Step 2: Open the source pull request**

Push without force. Open one pull request against `agoodkind/pr-agent:main`. Require the fork validation workflow to pass.

- [ ] **Step 3: Squash merge to one signed source patch commit**

The resulting commit must contain only the decision feature. Verify the remote commit signature and rerun the fork validation workflow on the merged commit.

- [ ] **Step 4: Create and protect the release input tag**

Create a signed annotated tag named `fork/review-decisions-v1` at the single merged patch commit. Push it without force.

Verify:

```bash
git fetch fork --tags
git verify-tag fork/review-decisions-v1
git cat-file tag fork/review-decisions-v1
```

Create or update an active repository ruleset that targets this exact tag and blocks deletion and non-fast-forward updates. Read the ruleset back through the GitHub API before continuing.

---

## Task 5: Extend safe upstream synchronization and image publication

**Files:**

- Create: `.github/scripts/prepare-upstream-release.sh`
- Create: `tests/fork/test_prepare_upstream_release.py`
- Modify: `.github/workflows/propose-upstream-release.yml`
- Modify: `.github/workflows/publish-fork-image.yml`

- [ ] **Step 1: Add a failing end-to-end release preparation test**

Use temporary Git repositories to exercise the shell helper through its command-line boundary. The test must prove:

- it applies `fork/lifecycle-v1` then `fork/review-decisions-v1`;
- it produces exactly two commits above the selected upstream release;
- it stops before output on a cherry-pick conflict;
- repeated preparation with fixed identities and dates produces the same commit SHA.

Run:

```bash
uv run pytest tests/fork/test_prepare_upstream_release.py -q
```

Expected: fail because the helper does not exist.

- [ ] **Step 2: Implement deterministic preparation**

Write a strict Bash script with `set -euo pipefail`. Accept exact upstream, lifecycle, and decision commit SHAs as required arguments. Apply plain `git cherry-pick` in the fixed order. Use fixed bot identity and committer dates. Assert exactly two commits above upstream and print only the prepared SHA on success.

Do not resolve conflicts, force-update refs, or fetch mutable branch names inside the helper.

- [ ] **Step 3: Verify both signed protected tags in the proposal workflow**

Update the read-only preparation job to:

1. resolve the exact upstream release tag;
2. resolve `fork/lifecycle-v1` and `fork/review-decisions-v1`;
3. verify both signed tag objects through Git and the GitHub API;
4. call the deterministic helper;
5. upload the prepared bundle before untrusted tests execute.

Keep tests in a read-only job. Keep the write token in a separate publish job that executes no prepared source. In the publish job, independently resolve both tags, rerun deterministic preparation, and require the resulting SHA to equal the tested artifact SHA before a non-force push.

The workflow may open a pull request and dispatch `fork-check.yml`. It must never merge the pull request.

- [ ] **Step 4: Version the immutable container release tag**

Change the fixed release tag to:

```text
upstream-v0.42.0-lifecycle-v1-review-decisions-v1
```

Keep `sha-${GITHUB_SHA}` as the canonical immutable source tag. Preserve the existing behavior that reuses an existing digest instead of republishing either immutable tag.

- [ ] **Step 5: Run workflow and behavior validation**

```bash
uv run pytest tests/fork/test_prepare_upstream_release.py -q
actionlint .github/workflows/propose-upstream-release.yml .github/workflows/publish-fork-image.yml .github/workflows/fork-check.yml
uv run pytest tests/unittest -q
docker build --target github_app -t pr-agent-review-decisions:test .
uv run pytest tests/fork/test_github_app_image.py -q
```

Expected: all commands pass.

- [ ] **Step 6: Commit the automation slice**

```bash
git add .github/scripts/prepare-upstream-release.sh tests/fork/test_prepare_upstream_release.py .github/workflows/propose-upstream-release.yml .github/workflows/publish-fork-image.yml
git commit -S -m "Update fork review decision release automation" -m "Co-authored-by: Codex <noreply@openai.com>"
```

- [ ] **Step 7: Open and merge the automation pull request**

Open a separate pull request after `fork/review-decisions-v1` exists. Wait for required checks. Merge normally. Do not deploy from an unmerged branch.

---

## Task 6: Enable decisions in the Cloudflare consumer

**Repository:** `/Users/agoodkind/Sites/pr-agent-cf`

**Files:**

- Modify: `worker/configuration.js`
- Modify: `test/router.test.js`
- Modify: `Dockerfile`

- [ ] **Step 1: Create an isolated consumer worktree from current remote main**

```bash
git fetch origin
```

Create the implementation worktree from `origin/main`. Do not build on a stale local `main` checkout.

- [ ] **Step 2: Add a failing consumer behavior test**

Update the configuration behavior test to require:

```javascript
assert.deepEqual(JSON.parse(environment.GITHUB_APP__PR_COMMANDS), ["/review"]);
assert.deepEqual(JSON.parse(environment.GITHUB_APP__PUSH_COMMANDS), ["/review"]);
assert.equal(environment.GITHUB__PUBLISH_REVIEW_DECISION, "true");
assert.equal(environment.GITHUB__REVIEW_DECISION_MIN_IMPORTANCE, "7");
```

Run:

```bash
npm test
```

Expected: fail because automatic commands still contain `/improve` and decision publication is disabled.

- [ ] **Step 3: Change configuration only**

Set both automatic command arrays to `["/review"]`. Add the two GitHub decision environment values from the test.

Do not remove `/improve` from PR-Agent. Manual `/improve` must continue to work.

- [ ] **Step 4: Pin the published image digest**

After the source image workflow publishes and the consumer update pull request opens, verify the digest resolves from GitHub Container Registry. Update the `Dockerfile` only to the proposed immutable digest.

- [ ] **Step 5: Run the full consumer validation**

```bash
npm run check
docker build -t pr-agent-cf-review-decisions:test .
docker inspect pr-agent-cf-review-decisions:test --format '{{.Config.User}} {{json .Config.ExposedPorts}}'
```

Expected: checks pass. The final image uses the configured non-root user and exposes port 3000.

- [ ] **Step 6: Commit and open the consumer pull request**

```bash
git add worker/configuration.js test/router.test.js Dockerfile
git commit -S -m "Enable GitHub review decisions" -m "Co-authored-by: Codex <noreply@openai.com>"
```

Push without force. Open a pull request. Wait for checks and merge normally. The existing push-to-main workflow performs the Cloudflare deployment.

---

## Task 7: Prove deployed behavior

**External systems:** GitHub App, Cloudflare deployment, temporary pull requests

- [ ] **Step 1: Verify deployment state before testing**

Confirm the consumer deployment workflow completed for the merged main commit. Probe the container-backed root route, not only the Worker-only `/health` route.

- [ ] **Step 2: Prove clean approval**

Open a temporary pull request with a harmless, reviewable change. Record:

- lifecycle transitions from queued to in progress to success;
- the existing persistent PR-Agent guide comment;
- one GitHub `APPROVED` review from the App;
- the analyzed head SHA in the review body.

- [ ] **Step 3: Prove inline change request**

Open or update a temporary pull request with a deterministic importance 7 or higher defect. Record:

- the existing persistent guide;
- at least one inline finding attached to a changed line;
- one `CHANGES_REQUESTED` review on the same head;
- successful lifecycle completion after publication.

- [ ] **Step 4: Prove new-head behavior**

Push a correction to the same pull request. Confirm the App analyzes the new head and publishes a fresh effective decision for that SHA. Confirm it does not duplicate the prior head's review on a retry.

- [ ] **Step 5: Prove manual behavior**

Invoke `/improve` manually and confirm it still runs without changing the review decision. Invoke `/review` as the pull request author or collaborator and confirm it can publish a decision.

- [ ] **Step 6: Remove unsafe test fixtures**

Close temporary pull requests without merging deliberate defects. Delete temporary branches after GitHub records the completed checks and reviews.
