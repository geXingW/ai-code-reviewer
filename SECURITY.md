# 安全策略

## 报告漏洞

如果你发现安全漏洞，**请勿**通过公开 Issue / PR / Discussion 披露。

请通过以下方式私下报告：

- Email: ge_xingw@163.com
- 主题：`[SECURITY] ai-code-reviewer - <简短描述>`

请在报告中包含：

- 漏洞类型（注入 / RCE / 权限提升 / 信息泄露 / ...）
- 复现步骤 / PoC
- 受影响版本
- 潜在影响

## 响应时间

- 24 小时内确认收到
- 7 天内给出初步评估
- 30 天内发布修复（视严重程度）

## 已知风险点

- **GitLab Access Token**：项目配置中存储，使用前必须在后端用对称加密落库
- **LLM API Key**：同上
- **Webhook Secret**：HMAC-SHA256 签名校验
- **SQL 注入**：使用 SQLAlchemy ORM，禁止字符串拼接
- **SSRF**：webhook 回调 URL / provider base_url 需校验非内网地址

谢谢配合。
