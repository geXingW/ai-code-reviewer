# GitLab Webhook 接入指南

> 适用范围：MVP 内网试运行。目标是让 GitLab MR 事件触发 AI review，并把结果写回 MR Discussion。

## 一、准备 GitLab Token

建议为机器人账号或项目/组级 Access Token 单独授权。

最小权限取决于 GitLab 版本和实例策略，通常需要覆盖：

- 读取项目与 MR 信息。
- 读取 MR diff / changes。
- 写入 MR Discussion 或 Note。
- 如后续启用 commit status，还需要写入 commit status 的权限。

安全建议：

- token 不写入仓库、不写入 Jenkinsfile 明文。
- token 只放在部署机 `.env`、Jenkins Credentials 或密钥管理系统中。
- 如果 token 曾在聊天、Issue、PR 评论中明文出现，建议立即 revoke 并重建。

## 二、配置服务端环境变量

在 `ai-code-reviewer` 部署目录编辑 `.env`：

```bash
GITLAB_BASE_URL=https://gitlab.example.com
GITLAB_TOKEN=<your-gitlab-token>
GITLAB_WEBHOOK_SECRET=<random-webhook-secret>
INTERNAL_API_TOKEN=<random-internal-token>
```

其中：

- `GITLAB_BASE_URL` 是 GitLab 实例根地址，不要带项目路径。
- `GITLAB_TOKEN` 用于后端读取 MR diff 并写回 Discussion。
- `GITLAB_WEBHOOK_SECRET` 用于校验 GitLab Webhook 请求头 `X-Gitlab-Token`。
- `INTERNAL_API_TOKEN` 用于管理台和 Jenkins 同步接口，不用于 GitLab Webhook。

修改后重启后端：

```bash
docker compose up -d --build backend
```

## 三、在 GitLab 项目中新增 Webhook

进入目标项目：

1. 打开 `Settings` → `Webhooks`。
2. URL 填写：`http://<backend-host>:8000/api/webhooks/gitlab`。
3. Secret Token 填写 `.env` 中的 `GITLAB_WEBHOOK_SECRET`。
4. Trigger 选择 `Merge request events`。
5. SSL verification 按内网证书情况选择。
6. 保存后使用 GitLab 的 Test 功能发送 Merge request event。

如果后端部署在反向代理后，URL 可改为 HTTPS 域名，例如：

```text
https://ai-reviewer.example.com/api/webhooks/gitlab
```

## 四、验证链路

先看服务健康状态：

```bash
curl -fsS http://<backend-host>:8000/health
```

预期返回包含：

```json
{"status":"ok","db":"ok","redis":"ok"}
```

再创建或更新一个测试 MR，观察：

- 后端日志中出现对应 `project_id` 和 `mr_iid`。
- GitLab MR 中出现 AI review 总结评论。
- 如果发现行级问题，GitLab MR diff 页面出现 Discussion。

查看日志：

```bash
docker compose logs -f backend
```

## 五、常见问题

**Webhook 测试返回 401**

- GitLab Webhook 的 Secret Token 与 `.env` 的 `GITLAB_WEBHOOK_SECRET` 不一致。
- 后端未重启，仍在使用旧环境变量。

**Webhook 测试超时**

- GitLab 服务器无法访问后端地址。
- 后端正在同步执行一次较慢的 review。MVP 阶段建议先用小 MR 验证。
- 网络层反向代理超时时间过短。

**MR 没有 Discussion**

- GitLab Token 权限不足或 token 已过期。
- MR diff 过大，被 diff 限制策略跳过。
- 当前模型/引擎返回无有效 finding。

**行级 Discussion 位置不正确**

- 确认当前版本使用的是 GitLab diff position 结构。
- 如果 GitLab 版本较老，建议先用小 diff MR 复现，并保留后端日志中的泛化错误摘要继续定位。

## 六、与 Jenkins 阻断的关系

GitLab Webhook 负责“收到 MR 事件后自动评审并评论”。

真正阻断合并通常由 Jenkins Pipeline 完成：

- Jenkins 调用 `POST /api/reviews` 同步拿到 review 结果。
- 如果响应中 `has_blocker=true`，Jenkins stage 失败。
- GitLab 分支保护要求 MR pipeline 必须成功，进而阻断合并。

因此，Webhook 和 Jenkins 可以同时启用：Webhook 提供及时评论，Jenkins 提供确定性合并阻断。
