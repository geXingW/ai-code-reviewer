# GitLab Webhook MVP

This document describes the current GitLab Merge Request webhook integration.
The implementation is intentionally an MVP: it validates incoming MR webhooks,
fetches MR diff from GitLab, runs the configured review engine, writes one
aggregate MR note, and sets a commit status.

## Runtime configuration

Set these environment variables before starting the backend:

```bash
GITLAB_BASE_URL=https://gitlab.example.com
GITLAB_TOKEN=glpat_xxx
GITLAB_WEBHOOK_SECRET=replace-with-a-random-shared-secret
INTERNAL_API_TOKEN=replace-with-a-random-jenkins-token
DEFAULT_REVIEW_ENGINE=llm-direct
```

Notes:

- `GITLAB_TOKEN` must be able to read merge request changes, create merge
  request notes, and set commit statuses for the target project.
- `GITLAB_WEBHOOK_SECRET` must match the Secret Token configured on the GitLab
  webhook.
- `DEFAULT_REVIEW_ENGINE` must match a registered `ReviewEngine` name.
- `INTERNAL_API_TOKEN` is used only by trusted server-to-server callers such as
  Jenkins. It is intentionally separate from the GitLab webhook secret.

## Jenkins synchronous review API

Jenkins pipelines can call `POST /api/reviews` and use the returned
`has_blocker` field to fail or pass the build. Unlike GitLab webhooks, this
endpoint runs synchronously so CI gets a deterministic result.

Required header:

- `X-Internal-Token`: same value as `INTERNAL_API_TOKEN`

Request body:

```json
{
  "project_id": 123,
  "mr_iid": 7,
  "target_branch": "master",
  "source_branch": "feature/demo",
  "commit_sha": "abc123",
  "target_commit_sha": "base456",
  "project_path": "group/demo",
  "title": "Demo MR",
  "web_url": "https://gitlab.example.com/group/demo/-/merge_requests/7"
}
```

Only `project_id`, `mr_iid`, `target_branch`, `source_branch`, and `commit_sha`
are required. Optional fields improve traceability in notes and response links.

Example:

```bash
curl -X POST http://localhost:8000/api/reviews \
  -H 'Content-Type: application/json' \
  -H "X-Internal-Token: ${INTERNAL_API_TOKEN}" \
  -d '{
    "project_id": 123,
    "mr_iid": 7,
    "target_branch": "master",
    "source_branch": "feature/demo",
    "commit_sha": "abc123",
    "web_url": "https://gitlab.example.com/group/demo/-/merge_requests/7"
  }'
```

A successful response looks like:

```json
{
  "review_id": "00000000-0000-0000-0000-000000000123",
  "status": "done",
  "has_blocker": false,
  "finding_count": 3,
  "blocker_count": 0,
  "policy_applied": "master -> BLOCKER",
  "review_url": "https://gitlab.example.com/group/demo/-/merge_requests/7#note_123"
}
```

Pipeline behavior recommendation:

- fail the Jenkins stage when `has_blocker` is `true`
- archive the full JSON response as a build artifact for audit
- set Jenkins timeout to at least 5 minutes for the synchronous API call

## GitLab webhook settings

In GitLab project settings, create a webhook with:

- URL: `https://your-backend.example.com/api/webhooks/gitlab`
- Secret Token: same value as `GITLAB_WEBHOOK_SECRET`
- Trigger: Merge request events
- SSL verification: enabled in production

## Supported events

The endpoint accepts only GitLab `Merge Request Hook` events with these actions:

- `open`
- `reopen`
- `update`

Other GitLab events are acknowledged with `processed=false` and ignored.

## Current processing flow

```text
GitLab MR webhook
  -> POST /api/webhooks/gitlab
  -> validate X-Gitlab-Token
  -> normalize payload into GitLabMergeRequestEvent
  -> fetch MR changes via GitLab API
  -> build ReviewContext
  -> run configured ReviewEngine
  -> match target branch against block policies
  -> create one MR note
  -> set commit status success/failed
```

## Branch block policy behavior

The orchestrator no longer hard-codes `BLOCKER` as the only merge-blocking
severity. It evaluates the target branch against ordered block policies and then
applies the matched severity threshold to engine findings.

Default policies seeded for new projects are:

- `master` -> block on `BLOCKER`
- `release/*` -> block on `BLOCKER`
- `hotfix/*` -> block on `BLOCKER`
- `*` -> `NONE`, allowing other branches by default

The reusable helper `build_default_block_policies(project_id)` creates the ORM
rows. Project CRUD persistence is still outside this MVP, so webhook processing
uses these defaults unless project-specific policies are injected by a later
repository/service layer.

## Current MVP limits

- Review work runs inline in the request path. A queue worker should replace
  this before high-volume production use.
- Review records and findings are not persisted by this orchestrator yet. The
  service returns a generated review UUID and can be wired to repositories later.
- The default `llm-direct` engine now performs real diff-only LLM review when
  `ReviewContext.provider` is populated. Until project/provider lookup is wired
  into the orchestrator, webhook runs without a provider degrade safely to no
  findings.
- Duplicate webhook delivery de-duplication is not implemented yet.

## Local smoke test

```bash
curl -X POST http://localhost:8000/api/webhooks/gitlab \
  -H 'Content-Type: application/json' \
  -H 'X-Gitlab-Event: Merge Request Hook' \
  -H "X-Gitlab-Token: ${GITLAB_WEBHOOK_SECRET}" \
  -d @sample-gitlab-mr-hook.json
```

A successful response looks like:

```json
{
  "processed": true,
  "reason": null,
  "status": "done",
  "finding_count": 0,
  "has_blocker": false,
  "note_id": 123
}
```
