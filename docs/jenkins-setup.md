# Jenkins Pipeline 接入指南

> 适用范围：MVP 内网试运行。目标是用 Jenkins 同步调用 AI review，并通过流水线状态阻断 GitLab MR 合并。

## 一、整体机制

GitLab Webhook 负责“自动评论”，Jenkins 负责“确定性阻断”：

1. GitLab MR 触发 Jenkins 构建。
2. Jenkins 在测试阶段调用 `POST /api/reviews`。
3. 后端同步评审 MR diff，返回 `has_blocker`、`finding_count` 等摘要。
4. Jenkins 根据 `has_blocker=true` 让 stage 失败。
5. GitLab 分支保护要求 MR Pipeline 成功，从而阻断合并。

## 二、Jenkins 凭据

建议在 Jenkins 中配置：

- `AI_REVIEWER_URL`：普通环境变量或参数，值为后端地址，例如 `http://ai-reviewer.example.com`。
- `AI_REVIEWER_INTERNAL_TOKEN`：Secret text 凭据，值为 `.env` 中的 `INTERNAL_API_TOKEN`。

不要把 token 明文写入 Jenkinsfile，也不要写进仓库。

## 三、Pipeline 示例

下面示例使用 GitLab 插件常见变量名。不同插件版本变量名可能不同，内网落地时以当前 Jenkins 环境实际变量为准。

```groovy
stage('AI Code Review') {
  steps {
    withCredentials([string(credentialsId: 'ai-reviewer-internal-token', variable: 'AI_REVIEWER_INTERNAL_TOKEN')]) {
      sh '''
        set -euo pipefail
        curl -fsS -X POST "$AI_REVIEWER_URL/api/reviews" \
          -H "Content-Type: application/json" \
          -H "X-Internal-Token: $AI_REVIEWER_INTERNAL_TOKEN" \
          -d "{\
            \"project_id\": ${gitlabMergeRequestTargetProjectId},\
            \"mr_iid\": ${gitlabMergeRequestIid},\
            \"target_branch\": \"${gitlabTargetBranch}\",\
            \"source_branch\": \"${gitlabSourceBranch}\",\
            \"commit_sha\": \"${GIT_COMMIT}\",\
            \"project_path\": \"${gitlabSourceNamespace}/${gitlabSourceRepoName}\",\
            \"title\": \"${gitlabMergeRequestTitle}\",\
            \"web_url\": \"${gitlabMergeRequestUrl}\"\
          }" | tee ai-review-result.json

        python - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path('ai-review-result.json').read_text(encoding='utf-8'))
if payload.get('has_blocker'):
    raise SystemExit('AI review found blocker issues')
PY
      '''
    }
  }
}
```

如果 Jenkins 环境没有 `gitlabMergeRequestTargetProjectId`，可以先在 MR 构建中打印环境变量名，但注意不要打印凭据：

```bash
printenv | sort | grep -E 'gitlab|GITLAB|CHANGE_|GIT_' | grep -vi 'token\|password\|secret'
```

## 四、Freestyle Job 接入

Freestyle Job 可以在 `Execute shell` 中调用同一段 `curl`，并把 `AI_REVIEWER_INTERNAL_TOKEN` 注入为 Secret text。

阻断逻辑保持一致：只要脚本退出码非 0，Jenkins 构建失败；GitLab 分支保护再阻断合并。

## 五、GitLab 合并阻断配置

在 GitLab 项目中建议开启：

- 目标分支保护：限制可直接 push 到 `master` / `release/*`。
- Merge request 必须 pipeline 成功后才能合并。
- 可选：要求 Code Owner 或指定 reviewer approval。

这样 AI review 的阻断结果会通过 Jenkins pipeline 状态传递到 GitLab。

## 六、试运行建议

- 先选一个非核心仓库或测试 MR 验证字段映射。
- 第一阶段只对 `master` 命中 `BLOCKER` 时阻断，其他分支只评论。
- 建议把 AI review stage 放在单元测试之后、部署之前，避免明显失败的构建浪费评审资源。
- 评审接口当前是同步执行；大 MR 可能耗时较长，Jenkins stage 超时时间建议先设为 10 到 15 分钟。

## 七、排错

**接口返回 401**

- `X-Internal-Token` 与后端 `.env` 的 `INTERNAL_API_TOKEN` 不一致。
- Jenkins credential 绑定变量名写错。

**接口返回 422**

- 请求 JSON 字段缺失或类型不对。
- `project_id`、`mr_iid` 必须是正整数。
- `target_branch`、`source_branch`、`commit_sha` 不能为空。

**Jenkins stage 超时**

- 先用小 MR 验证。
- 检查后端是否能访问 GitLab。
- 查看 `docker compose logs -f backend` 中该 MR 的安全摘要。

**GitLab 没有阻断合并**

- 确认 Jenkins 构建已经被 GitLab MR 识别为该 MR 的 pipeline/status。
- 确认 GitLab 项目设置里要求 pipeline 成功后才允许合并。
- 确认目标分支保护规则没有被 Maintainer 绕过。
