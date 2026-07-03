/**
 * ai-code-reviewer — AI Review Stage Only
 *
 * Paste this stage into your existing Jenkinsfile if you only need
 * the AI Code Review step without rebuilding the whole pipeline.
 *
 * Prerequisites:
 *   - Secret text credential "ai-reviewer-internal-token" configured in Jenkins
 *   - AI_REVIEWER_URL environment variable set (backend service URL)
 *   - GitLab plugin providing MR environment variables
 */

stage('AI Code Review') {
    steps {
        withCredentials([string(credentialsId: 'ai-reviewer-internal-token', variable: 'AI_REVIEWER_INTERNAL_TOKEN')]) {
            sh '''#!/bin/bash
                set -euo pipefail

                echo "=== AI Code Review ==="

                # --- Adjust these variable names to match your GitLab plugin version ---
                PROJECT_ID="${gitlabMergeRequestTargetProjectId:-}"
                MR_IID="${gitlabMergeRequestIid:-}"
                TARGET_BRANCH="${gitlabTargetBranch:-}"
                SOURCE_BRANCH="${gitlabSourceBranch:-}"
                COMMIT_SHA="${GIT_COMMIT:-}"
                PROJECT_PATH="${gitlabSourceNamespace}/${gitlabSourceRepoName}"
                MR_TITLE="${gitlabMergeRequestTitle:-}"
                MR_URL="${gitlabMergeRequestUrl:-}"

                # --- Validate required fields ---
                if [ -z "$PROJECT_ID" ] || [ -z "$MR_IID" ] || [ -z "$COMMIT_SHA" ]; then
                    echo "ERROR: Missing GitLab MR environment variables."
                    echo "Available gitlab vars:"
                    printenv | sort | grep -iE 'gitlab|GIT_' | grep -vi 'token\|password\|secret' || true
                    exit 1
                fi

                # --- Call AI Reviewer API ---
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

                # --- Parse result and block merge if blockers found ---
                python3 - <<'PY'
import json, sys
from pathlib import Path

result = json.loads(Path("ai-review-result.json").read_text(encoding="utf-8"))
has_blocker = result.get("has_blocker", False)
finding_count = result.get("finding_count", 0)
blocker_count = result.get("blocker_count", 0)

print(f"Review status: {result.get('status', 'unknown')}")
print(f"Findings: {finding_count} total, {blocker_count} blockers")
print(f"Block policy: {result.get('policy_applied', 'unknown')}")

if result.get("review_url"):
    print(f"Review URL: {result.get('review_url')}")

if has_blocker:
    print("\n❌ AI Review FAILED: blocker issues found. Merge is blocked.")
    sys.exit(1)
else:
    print("\n✅ AI Review PASSED: no blocker issues.")
PY
            '''
        }
    }
}
