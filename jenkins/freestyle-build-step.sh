#!/bin/bash
#
# ai-code-reviewer — Freestyle Job Build Step
#
# For Jenkins Freestyle projects that don't use Pipeline.
# Paste this into "Execute shell" build step.
#
# Prerequisites:
#   - Configure AI_REVIEWER_URL as a job parameter or environment variable
#   - Bind AI_REVIEWER_INTERNAL_TOKEN as a Secret text credential injection
#   - GitLab plugin must provide MR variables (or set them manually below)
#

set -euo pipefail

echo "=== AI Code Review (Freestyle) ==="

# --- Configuration ---
# Override these if GitLab plugin variables are not available
PROJECT_ID="${gitlabMergeRequestTargetProjectId:-${GITLAB_PROJECT_ID:-}}"
MR_IID="${gitlabMergeRequestIid:-${GITLAB_MR_IID:-}}"
TARGET_BRANCH="${gitlabTargetBranch:-${GITLAB_TARGET_BRANCH:-master}}"
SOURCE_BRANCH="${gitlabSourceBranch:-${GITLAB_SOURCE_BRANCH:-}}"
COMMIT_SHA="${GIT_COMMIT:-${GITLAB_COMMIT_SHA:-}}"
PROJECT_PATH="${gitlabSourceNamespace}/${gitlabSourceRepoName}"
MR_TITLE="${gitlabMergeRequestTitle:-AI Review}"
MR_URL="${gitlabMergeRequestUrl:-}"

# --- Validate ---
if [ -z "$PROJECT_ID" ] || [ -z "$MR_IID" ] || [ -z "$COMMIT_SHA" ]; then
    echo "ERROR: Missing required GitLab MR variables."
    echo ""
    echo "Required variables:"
    echo "  gitlabMergeRequestTargetProjectId (or GITLAB_PROJECT_ID)"
    echo "  gitlabMergeRequestIid (or GITLAB_MR_IID)"
    echo "  GIT_COMMIT (or GITLAB_COMMIT_SHA)"
    echo ""
    echo "Available gitlab vars:"
    printenv | sort | grep -iE 'gitlab|GIT_' | grep -vi 'token\|password\|secret' || true
    exit 1
fi

if [ -z "$AI_REVIEWER_URL" ] || [ -z "$AI_REVIEWER_INTERNAL_TOKEN" ]; then
    echo "ERROR: AI_REVIEWER_URL or AI_REVIEWER_INTERNAL_TOKEN not set."
    echo "Configure them as Jenkins environment variables / credential bindings."
    exit 1
fi

echo "Project: $PROJECT_PATH (ID: $PROJECT_ID)"
echo "MR: !$MR_IID ($SOURCE_BRANCH → $TARGET_BRANCH)"
echo "Commit: $COMMIT_SHA"
echo ""

# --- Call AI Reviewer API ---
echo "Calling AI Reviewer..."
curl -fsS -X POST "$AI_REVIEWER_URL/api/reviews" \
    -H "Content-Type: application/json" \
    -H "X-Internal-Token: $AI_REVIEWER_INTERNAL_TOKEN" \
    -d "{
        \"project_id\": $PROJECT_ID,
        \"mr_iid\": $MR_IID,
        \"target_branch\": \"$TARGET_BRANCH\",
        \"source_branch\": \"$SOURCE_BRANCH\",
        \"commit_sha\": \"$COMMIT_SHA\",
        \"project_path\": \"$PROJECT_PATH\",
        \"title\": \"$MR_TITLE\",
        \"web_url\": \"$MR_URL\"
    }" | tee ai-review-result.json

echo ""

# --- Parse result ---
python3 - <<'PY'
import json, sys
from pathlib import Path

result = json.loads(Path("ai-review-result.json").read_text(encoding="utf-8"))

has_blocker = result.get("has_blocker", False)
finding_count = result.get("finding_count", 0)
blocker_count = result.get("blocker_count", 0)
status = result.get("status", "unknown")
policy = result.get("policy_applied", "unknown")
review_url = result.get("review_url", "")

print(f"Review Status : {status}")
print(f"Findings      : {finding_count} total, {blocker_count} blockers")
print(f"Block Policy  : {policy}")
if review_url:
    print(f"Review URL    : {review_url}")
print()

if has_blocker:
    print("❌ AI Review FAILED: blocker issues found. Merge is blocked.")
    sys.exit(1)
else:
    print("✅ AI Review PASSED: no blocker issues.")
PY
