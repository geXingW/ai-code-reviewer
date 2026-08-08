## Merge Request Context

- MR title: {{mr_title}}
- Source branch: {{source_branch}}
- Target branch: {{target_branch}}

### MR description

{{mr_description}}

## Diff

{{diff_block}}

## Candidate findings

{{candidate_findings_block}}

## Task

Adversarially review each candidate finding above. For every finding you
consider disproving or downgrading, emit a decision entry. Omit findings you
would otherwise mark `keep`. Return decisions strictly following the JSON
contract in the system prompt.
