# GitHub review decisions

## Goal

PR-Agent keeps its persistent review guide and publishes key findings inline. It also
submits one GitHub review decision for the pull request head that it analyzed.

The feature is opt-in and GitHub-specific. Existing installations keep their current
behavior until they enable it.

## Scope

The fork adds decisions to automatic and manual `/review` runs. It does not change
manual `/improve` behavior or add another model call.

The Cloudflare consumer runs only `/review` automatically. Users may still invoke
`/improve` manually. This is a deployment configuration change, not part of the
upstream fork patch.

## Review output

A successful `/review` run publishes the existing persistent guide first. Each valid
key finding also becomes an inline GitHub review comment on the affected diff lines.
Findings that GitHub cannot attach to the diff remain visible in the persistent guide.

The existing structured review response gains one integer `importance` field from 1
through 10 for every key finding. The same model response supplies this value. The
patch does not parse published Markdown and does not call the model again.

## Decision policy

The decision uses the parsed review response and the review tool's existing list of
files excluded by its token budget.

| Evidence | GitHub event |
| --- | --- |
| The review covered the full diff and found no key issues | `APPROVE` |
| Any key issue has importance 7 or higher | `REQUEST_CHANGES` |
| Findings are below importance 7 | `COMMENT` |
| Review coverage is incomplete | `COMMENT` |

The policy applies `REQUEST_CHANGES` before `COMMENT`, then allows `APPROVE` only
when neither condition applies. A high-importance finding therefore requests changes
even when coverage is incomplete. A missing or invalid importance value produces
`COMMENT`.

An analysis, parsing, or publication failure never approves the pull request. The
automatic lifecycle check fails when the run cannot produce or publish its required
output.

The review body gives the decision reason and identifies the analyzed head commit.
Inline comments and the decision are submitted in one GitHub review so they refer to
the same head.

## Triggers and authorization

Automatic reviews run when a pull request opens, reopens, becomes ready, or receives
a new push. Every push analyzes the new head and publishes a fresh effective decision.

Manual `/review` keeps its existing review comments. The GitHub App publishes a
decision only when the command author is either the pull request author or a repository
collaborator. Other eligible commenters retain existing review output without gaining
the ability to trigger `APPROVE` or `REQUEST_CHANGES`.

Manual `/improve` remains available and never changes the review decision.

## Components

### Review assessment

A small pure policy component accepts parsed key findings and the uncovered-file list.
It returns the GitHub event, a reason, and validated inline finding descriptors. It has
no GitHub or model dependency.

### GitHub publisher

The GitHub provider converts validated findings into review comments and submits one
review against the exact analyzed commit. It reuses the provider's existing diff-line
validation. A finding rejected by GitHub's inline-comment rules stays in the persistent
guide and still contributes to the decision.

### GitHub App context

The webhook handler determines whether a manual command author may publish decisions.
It passes that capability into the review run without weakening authorization for any
other command.

### Configuration

`github.publish_review_decision` enables review decisions and defaults to false.
`github.review_decision_min_importance` defaults to 7. When decision publication is
disabled, the importance field is absent from the prompt and upstream review behavior
remains unchanged.

The Cloudflare consumer enables the feature and changes automatic pull request and push
commands to `["/review"]`. No automatic `/improve` command remains.

## Failure behavior

The patch fails closed:

- Missing or invalid importance values produce `COMMENT`.
- Incomplete coverage produces `COMMENT`, never `APPROVE`.
- Invalid inline locations remain in the guide and do not suppress the decision.
- A GitHub review write failure fails the automatic lifecycle check.
- A stale head detected before publication aborts the decision instead of reviewing a
  different commit.

## Tests

Focused behavior tests cover:

- full coverage with no findings produces `APPROVE`;
- a finding at importance 7 produces `REQUEST_CHANGES`;
- lower importance findings produce `COMMENT`;
- incomplete coverage produces `COMMENT`;
- invalid or missing importance never produces `APPROVE`;
- inline findings and the decision target the same head commit;
- invalid inline locations stay in the guide;
- authorized manual actors publish decisions;
- other manual actors publish comments without a decision;
- a pushed head receives a new decision;
- publication failure fails the automatic lifecycle;
- the disabled configuration preserves upstream behavior.

The fork validation workflow runs the full upstream unit suite and builds and starts the
exact `github_app` image. Live acceptance uses a temporary pull request to prove a clean
approval, a high-importance inline change request, a new-head decision, and successful
lifecycle completion.

## Release automation

The decision patch receives the signed protected tag `fork/review-decisions-v1`. The
upstream release proposal workflow verifies both protected tag objects, applies
`fork/lifecycle-v1` and `fork/review-decisions-v1` in fixed order, stops on conflict,
runs validation, and opens a pull request. It never resolves conflicts, force-pushes,
auto-merges, or publishes an unmerged image.
