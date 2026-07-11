## Merge Request Context

- MR title: {{mr_title}}
- Source branch: {{source_branch}}
- Target branch: {{target_branch}}
- Source commit: {{source_commit_sha}}
- Target commit: {{target_commit_sha}}

### MR description

{{mr_description}}

### Latest commit message

{{last_commit_message}}

## Active Rules

{{rules_block}}

## False-positive history (do not re-report these)

{{history_block}}

## Diff

{{diff_block}}

## Task

Review only lines added or modified by the diff above. Return findings strictly following the JSON contract in the system prompt.
