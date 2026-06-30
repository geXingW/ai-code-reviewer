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
DEFAULT_REVIEW_ENGINE=llm-direct
```

Notes:

- `GITLAB_TOKEN` must be able to read merge request changes, create merge
  request notes, and set commit statuses for the target project.
- `GITLAB_WEBHOOK_SECRET` must match the Secret Token configured on the GitLab
  webhook.
- `DEFAULT_REVIEW_ENGINE` must match a registered `ReviewEngine` name.

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
  -> create one MR note
  -> set commit status success/failed
```

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
