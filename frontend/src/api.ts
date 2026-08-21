export type HealthStatus = {
  status: string;
  version: string;
  db: string;
};

export type EngineSummary = {
  name: string;
  supports_feedback: boolean;
  requires_repo_clone: boolean;
  healthy: boolean;
  health_status: string;
};

export type RecentReview = {
  review_id: string | null;
  project_id: number;
  project_path: string;
  mr_iid: number;
  title: string;
  web_url: string | null;
  status: string;
  has_blocker: boolean;
  finding_count: number;
  blocker_count: number;
  policy_applied: string | null;
  review_url: string | null;
  created_at?: string;
  engine_used?: string | null;
  // PR #89：首页最近评审面板也需要展示"增量 / 复用"徽章。
  review_mode?: 'full' | 'incremental' | 'reuse' | null;
  // PR #96：MR 生命周期事件记账 Review 的标签。有值时前端优先渲染专属徽章，
  // 替代（不并列）review_mode 徽章。老数据 / 普通审查为 null / undefined。
  lifecycle_event?: 'mr_closed' | 'mr_merged' | null;
};

export type CreateReviewPayload = {
  project_id: number;
  mr_iid: number;
  target_branch: string;
  source_branch: string;
  commit_sha: string;
  project_path?: string;
  title?: string;
  web_url?: string;
};

export type CreateReviewResponse = {
  review_id: string | null;
  status: string;
  has_blocker: boolean;
  finding_count: number;
  blocker_count: number;
  policy_applied: string | null;
  review_url: string | null;
};

export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

export type ProviderConfig = {
  id: string;
  name: string;
  protocol: string;
  base_url: string;
  api_key: string;
  model: string;
  temperature: number;
  max_tokens: number;
  enabled: boolean;
  created_at?: string;
};

export type RuleConfig = {
  id: string;
  rule_id: string;
  title: string;
  prompt_snippet: string;
  severity_default: string;
  category_default?: string | null;  // PR #100 引入：规则默认分类（security/bug/…）。
  languages: unknown[];
  path_patterns: unknown[];
  // 自定义标签，用于规则列表与项目规则选择面板按标签筛选。
  tags: string[];
  enabled: boolean;
  grace_period_until?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type RuleFormPayload = {
  rule_id: string;
  title: string;
  prompt_snippet: string;
  severity_default: 'INFO' | 'WARNING' | 'BLOCKER';
  // 自定义标签；新增/编辑规则弹窗维护，提交时透传给后端。
  tags: string[];
  enabled: boolean;
};

export type BlockPolicySeverity =
  | 'NONE'
  | 'INFO'
  | 'WARNING'
  | 'BLOCKER'
  | 'ENGINE_ERROR_ONLY';

export type BlockPolicy = {
  id: string;
  project_id: string | null;
  branch_pattern: string;
  block_severity: BlockPolicySeverity;
  block_on_engine_error: boolean;
  require_all_resolved: boolean;
  priority: number;
  created_at?: string;
  updated_at?: string;
};

export type BlockPolicyPayload = {
  branch_pattern: string;
  block_severity: BlockPolicySeverity;
  block_on_engine_error: boolean;
  require_all_resolved: boolean;
  priority: number;
};

export type ProjectRuleConfig = {
  project_id: string;
  rule_id: string;
  enabled: boolean;
  severity_override: 'INFO' | 'WARNING' | 'BLOCKER' | null;
  created_at?: string;
  updated_at?: string;
};

export type ProjectRuleFormPayload = {
  rule_id: string;
  enabled: boolean;
};

export type ProjectConfig = {
  id: string;
  name: string;
  gitlab_project_id: string;
  gitlab_base_url: string;
  gitlab_access_token: string;
  webhook_secret: string;
  engine_id: string | null;
  provider_id: string | null;
  enabled: boolean;
  default_block_severity: string;
  timeout_seconds: number;
  max_files: number;
  // 项目级 commit 审查开关（GitLab Push Hook 逐 commit 审查）。
  commit_review_enabled: boolean;
  commit_review_max_per_push: number;
  ignore_paths: unknown[] | null;
  rules: ProjectRuleConfig[];
  block_policies: BlockPolicy[];
  notification_channels: NotificationChannel[];
  created_at?: string;
  updated_at?: string;
};

export type ReviewRecord = {
  id: string;
  project_id: string;
  mr_iid: string;
  source_branch: string;
  target_branch: string;
  commit_sha: string;
  status: string;
  has_blocker: boolean;
  finding_count: number;
  // Issue #71：展示用冗余字段，由后端 ReviewRead 填充。
  project_name?: string | null;
  rules_used?: string[];
  created_at?: string;
  // Issue #76：展示评审所用引擎，便于对比多引擎运行结果。
  engine_used?: string | null;
  // PR #89：增量审查串链元数据，用于列表 UI 展示"全量 / 增量 / 复用"徽章
  // 及"接续自 <parent_id>"提示。老数据 review_mode 会走 server_default 'full'。
  base_sha?: string | null;
  parent_review_id?: string | null;
  review_mode?: 'full' | 'incremental' | 'reuse' | null;
  // PR #96：MR 生命周期事件记账 Review 的标签。有值时优先渲染专属徽章，
  // 替代（不并列）review_mode 徽章。老数据 / 普通审查为 null / undefined。
  lifecycle_event?: 'mr_closed' | 'mr_merged' | null;
};

export type FindingRecord = {
  id: string;
  review_id: string;
  file_path: string;
  line_number?: number | null;
  rule_id: string;
  severity: string;
  title: string;
  description?: string | null;
  suggestion?: string | null;
  existing_code?: string | null;
  fp_status: string;
  // finding 生命周期状态：
  // - ``open``：活着的问题（默认，UI 不渲染徽章）；
  // - ``resolved``：已修（含 MR merged 情形）；
  // - ``mr_closed``：所属 MR 被关闭（非合并）。
  // 老数据可能不带此字段，UI 需按 undefined/null 走 open 语义。
  status?: 'open' | 'resolved' | 'mr_closed' | null;
  // PR #100 引入：finding 的分类（security/bug/performance/maintainability/style/other）。
  // 老数据可能不带此字段，UI 应用 categoryDisplay 做未知兜底。
  category?: string | null;
  fp_marked_by?: string | null;
  fp_marked_at?: string | null;
  fp_marked_reason?: string | null;
  fp_reviewed_by?: string | null;
  fp_reviewed_at?: string | null;
  fp_review_note?: string | null;
  created_at?: string;
  // 展示用冗余字段：由后端 admin API 的 _finding_to_read 填充，用于问题与误报
  // 列表页快速定位到项目 / MR。mr_title 目前后端始终返回 null（Review 表未落库
  // 该列），UI 需要兜底走 mr_iid。老数据 review_created_at 有落库时间可用。
  project_name?: string | null;
  project_id?: string | null;
  mr_iid?: string | null;
  mr_title?: string | null;
  review_created_at?: string | null;
};

export type NegativeExample = {
  id: string;
  rule_id: string;
  project_id?: string | null;
  code_snippet: string;
  explanation?: string | null;
  source_finding_id?: string | null;
  approved_by?: string | null;
  created_at?: string;
};

export type EngineConfig = {
  id: string;
  name: string;
  description?: string | null;
  enabled: boolean;
  config?: Record<string, unknown> | null;
  created_at?: string;
};

export type ProviderFormPayload = {
  name: string;
  protocol: 'openai_compatible' | 'anthropic' | 'custom';
  base_url: string;
  api_key: string;
  model: string;
  temperature: number;
  max_tokens: number;
  enabled: boolean;
};

export type ProjectFormPayload = {
  name: string;
  gitlab_project_id: string;
  gitlab_base_url: string;
  gitlab_access_token: string;
  webhook_secret: string;
  engine_id: string;
  provider_id: string;
  enabled: boolean;
  timeout_seconds: number;
  max_files: number;
  commit_review_enabled: boolean;
  commit_review_max_per_push: number;
  default_block_severity: 'INFO' | 'WARNING' | 'BLOCKER';
  rules: ProjectRuleFormPayload[];
};

export type ProjectUpdatePayload = {
  name?: string;
  gitlab_project_id?: string;
  gitlab_base_url?: string;
  gitlab_access_token?: string;
  webhook_secret?: string;
  enabled?: boolean;
  default_block_severity?: 'INFO' | 'WARNING' | 'BLOCKER';
  commit_review_enabled?: boolean;
  commit_review_max_per_push?: number;
  engine_id?: string | null;
  provider_id?: string | null;
  rules?: ProjectRuleFormPayload[];
  block_policies?: BlockPolicyPayload[];
};

export type FalsePositiveMarkPayload = {
  marked_by: string;
  reason?: string;
};

export type ResolvePayload = {
  resolved_by: string;
  reason?: string;
};

export type FalsePositiveReviewPayload = {
  reviewed_by: string;
  note?: string;
};

export type LoginResponse = {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  // PR-B：后端登录返回当前用户名，前端存到 sessionStorage 后用作
  // 误报标记/审核弹窗中"标记人 / 审核人"的默认值。
  username: string;
};

const ADMIN_TOKEN_STORAGE_KEY = 'aicr_admin_access_token';
// PR-B：与 token 同层 sessionStorage 键，登出时一起清空。
const ADMIN_USERNAME_STORAGE_KEY = 'aicr_admin_username';

export class AuthRequiredError extends Error {
  constructor(message = '登录已过期，请重新登录。') {
    super(message);
    this.name = 'AuthRequiredError';
  }
}

export function isAuthRequiredError(error: unknown): error is AuthRequiredError {
  return error instanceof AuthRequiredError;
}

export function getStoredAdminAccessToken(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  return window.sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) ?? '';
}

export function setStoredAdminAccessToken(token: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  const trimmedToken = token.trim();
  if (trimmedToken) {
    window.sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, trimmedToken);
  } else {
    window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
  }
}

export function clearStoredAdminAccessToken(): void {
  setStoredAdminAccessToken('');
  // PR-B：登出时把用户名也一起清掉，保持"登出后什么都不残留"的语义。
  clearStoredAdminUsername();
}

// PR-B：sessionStorage 里的当前登录用户名。仅用于前端默认值预填（例如误报
// 处理弹窗里的"标记人 / 审核人"输入框），不参与服务端鉴权。
export function getStoredAdminUsername(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  return window.sessionStorage.getItem(ADMIN_USERNAME_STORAGE_KEY) ?? '';
}

export function setStoredAdminUsername(username: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  const trimmed = username.trim();
  if (trimmed) {
    window.sessionStorage.setItem(ADMIN_USERNAME_STORAGE_KEY, trimmed);
  } else {
    window.sessionStorage.removeItem(ADMIN_USERNAME_STORAGE_KEY);
  }
}

export function clearStoredAdminUsername(): void {
  setStoredAdminUsername('');
}

function buildAdminHeaders(extraHeaders: Record<string, string> = {}): Record<string, string> {
  const token = getStoredAdminAccessToken();
  if (!token) {
    throw new AuthRequiredError('请先登录管理台。');
  }
  return {
    ...extraHeaders,
    Authorization: `Bearer ${token}`,
  };
}

async function adminFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const extraHeaders = (init.headers ?? {}) as Record<string, string>;
  return fetch(input, {
    ...init,
    headers: buildAdminHeaders(extraHeaders),
  });
}

async function parseJsonResponse<T>(response: Response, authProtected = false): Promise<T> {
  const payload = (await response.json()) as T | { detail?: string };
  if (!response.ok) {
    const detail =
      typeof (payload as { detail?: string }).detail === 'string'
        ? (payload as { detail: string }).detail
        : `HTTP ${response.status}`;
    if (authProtected && (response.status === 401 || response.status === 403)) {
      throw new AuthRequiredError(detail || '登录已过期，请重新登录。');
    }
    throw new Error(detail);
  }
  return payload as T;
}

export async function loginAdmin(username: string, password: string): Promise<LoginResponse> {
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const payload = await parseJsonResponse<LoginResponse>(response);
  setStoredAdminAccessToken(payload.access_token);
  // PR-B：后端在 LoginResponse 里带回 username，存下来作为误报处理弹窗默认值。
  // 老服务或测试 mock 可能没有该字段，走 username 兜底避免 trim() 崩。
  setStoredAdminUsername(payload.username ?? username ?? '');
  return payload;
}

export async function fetchHealth(): Promise<HealthStatus> {
  const response = await fetch('/health');
  return parseJsonResponse<HealthStatus>(response);
}

export async function fetchEngines(): Promise<EngineSummary[]> {
  const response = await adminFetch('/api/engines');
  return parseJsonResponse<EngineSummary[]>(response, true);
}

export async function fetchRecentReviews(): Promise<RecentReview[]> {
  const response = await adminFetch('/api/reviews/recent');
  return parseJsonResponse<RecentReview[]>(response, true);
}

export async function createReview(
  payload: CreateReviewPayload,
  internalToken: string,
): Promise<CreateReviewResponse> {
  const response = await fetch('/api/reviews', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Internal-Token': internalToken,
    },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<CreateReviewResponse>(response);
}

export async function fetchProviders(): Promise<Page<ProviderConfig>> {
  const response = await adminFetch('/api/providers');
  return parseJsonResponse<Page<ProviderConfig>>(response, true);
}

export async function createProvider(payload: ProviderFormPayload): Promise<ProviderConfig> {
  const response = await adminFetch('/api/providers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<ProviderConfig>(response, true);
}

export type ProviderUpdatePayload = {
  name?: string;
  protocol?: ProviderFormPayload['protocol'];
  base_url?: string;
  api_key?: string;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  enabled?: boolean;
};

export async function updateProvider(
  id: string,
  payload: ProviderUpdatePayload,
): Promise<ProviderConfig> {
  const response = await adminFetch(`/api/providers/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<ProviderConfig>(response, true);
}

/**
 * 拉规则列表。规则关联面板要显示所有规则，后端 `limit` 上限 100，
 * 超过 100 条时循环 offset 拿全。避免面板显示不全导致老 UI 漏勾选。
 */
export async function fetchRules(): Promise<Page<RuleConfig>> {
  const pageSize = 100;
  let offset = 0;
  const items: RuleConfig[] = [];
  let total = 0;
  while (true) {
    const response = await adminFetch(`/api/rules?limit=${pageSize}&offset=${offset}`);
    const page = await parseJsonResponse<Page<RuleConfig>>(response, true);
    items.push(...page.items);
    total = page.total;
    if (page.items.length < pageSize || items.length >= total) {
      break;
    }
    offset += pageSize;
  }
  return { items, total, limit: pageSize, offset: 0 };
}

// Issue #69：rule_id 可选，留空时由后端从标题自动生成 slug。
export type RuleCreatePayload = Omit<RuleFormPayload, 'rule_id'> & { rule_id?: string };

export async function createRule(payload: RuleCreatePayload): Promise<RuleConfig> {
  const response = await adminFetch('/api/rules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<RuleConfig>(response, true);
}

export type RuleUpdatePayload = Partial<RuleFormPayload>;

export async function updateRule(id: string, payload: RuleUpdatePayload): Promise<RuleConfig> {
  const response = await adminFetch(`/api/rules/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<RuleConfig>(response, true);
}

export async function deleteRule(id: string): Promise<void> {
  const response = await adminFetch(`/api/rules/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    throw new Error(`删除规则失败：HTTP ${response.status}`);
  }
}

export async function fetchProjects(): Promise<Page<ProjectConfig>> {
  const response = await adminFetch('/api/projects');
  return parseJsonResponse<Page<ProjectConfig>>(response, true);
}

export async function createProject(payload: ProjectFormPayload): Promise<ProjectConfig> {
  const body: Record<string, unknown> = {
    name: payload.name,
    gitlab_project_id: payload.gitlab_project_id,
    gitlab_access_token: payload.gitlab_access_token,
    webhook_secret: payload.webhook_secret,
    enabled: payload.enabled,
    timeout_seconds: payload.timeout_seconds,
    max_files: payload.max_files,
    commit_review_enabled: payload.commit_review_enabled,
    commit_review_max_per_push: payload.commit_review_max_per_push,
    default_block_severity: payload.default_block_severity,
  };
  // 仅当用户主动选了规则时才传 rules；为空时不传，让后端走「安全默认」
  // 策略（自动关联所有启用的 BLOCKER 规则）。
  if (payload.rules.length > 0) {
    body.rules = payload.rules;
  }
  if (payload.engine_id) {
    body.engine_id = payload.engine_id;
  }
  if (payload.provider_id) {
    body.provider_id = payload.provider_id;
  }
  const response = await adminFetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseJsonResponse<ProjectConfig>(response, true);
}

export async function updateProject(
  projectId: string,
  payload: ProjectUpdatePayload,
): Promise<ProjectConfig> {
  const response = await adminFetch(`/api/projects/${projectId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<ProjectConfig>(response, true);
}

// Issue #73：项目删除。参照 deleteRule 的写法，成功返回 204/200 即可。
export async function deleteProject(id: string): Promise<void> {
  const response = await adminFetch(`/api/projects/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    throw new Error(`删除项目失败：HTTP ${response.status}`);
  }
}

export async function fetchReviewRecords(): Promise<Page<ReviewRecord>> {
  const response = await adminFetch('/api/reviews/records');
  return parseJsonResponse<Page<ReviewRecord>>(response, true);
}

export async function fetchFindings(fpStatus?: string): Promise<Page<FindingRecord>> {
  const suffix = fpStatus ? `?fp_status=${encodeURIComponent(fpStatus)}` : '';
  const response = await adminFetch(`/api/findings${suffix}`);
  return parseJsonResponse<Page<FindingRecord>>(response, true);
}

export async function fetchReviewFindings(reviewId: string): Promise<FindingRecord[]> {
  const suffix = `?review_id=${encodeURIComponent(reviewId)}&limit=100`;
  const response = await adminFetch(`/api/findings${suffix}`);
  const page = await parseJsonResponse<Page<FindingRecord>>(response, true);
  return page.items;
}

export async function markFalsePositive(
  findingId: string,
  payload: FalsePositiveMarkPayload,
): Promise<FindingRecord> {
  const response = await adminFetch(`/api/findings/${findingId}/false-positive`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<FindingRecord>(response, true);
}

export async function resolveFinding(
  findingId: string,
  payload: ResolvePayload,
): Promise<FindingRecord> {
  const response = await adminFetch(`/api/findings/${findingId}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<FindingRecord>(response, true);
}

export async function fetchPendingFalsePositives(): Promise<Page<FindingRecord>> {
  const response = await adminFetch('/api/false-positives/pending');
  return parseJsonResponse<Page<FindingRecord>>(response, true);
}

export async function confirmFalsePositive(
  findingId: string,
  payload: FalsePositiveReviewPayload,
): Promise<FindingRecord> {
  const response = await adminFetch(`/api/false-positives/${findingId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<FindingRecord>(response, true);
}

export async function rejectFalsePositive(
  findingId: string,
  payload: FalsePositiveReviewPayload,
): Promise<FindingRecord> {
  const response = await adminFetch(`/api/false-positives/${findingId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<FindingRecord>(response, true);
}

export async function fetchNegativeExamples(): Promise<Page<NegativeExample>> {
  const response = await adminFetch('/api/negative-examples');
  return parseJsonResponse<Page<NegativeExample>>(response, true);
}

export async function fetchEngineConfigs(): Promise<Page<EngineConfig>> {
  const response = await adminFetch('/api/engines/configs');
  return parseJsonResponse<Page<EngineConfig>>(response, true);
}

// ---------------- 统计聚合 API（/api/stats/*）----------------
//
// 后端聚合口径：
// - review 相关统计已排除 lifecycle_event NOT NULL 的 MR 生命周期记账。
// - engine/provider 缺失归入 "unknown"。
// - fp_rate / percentage 分母为 0 时后端返回 0.0（前端不再兜底除零）。

export type EngineUsageStat = { engine: string; count: number };
export type ProviderUsageStat = { provider: string; count: number };
export type StatusBreakdownStat = { status: string; count: number };

export type StatsOverview = {
  days: number;
  since: string;
  total_reviews: number;
  total_findings: number;
  total_blockers: number;
  total_resolved: number;
  avg_duration_ms: number | null;
  active_projects: number;
  fp_pending: number;
  fp_confirmed: number;
  fp_rejected: number;
  engine_usage: EngineUsageStat[];
  provider_usage: ProviderUsageStat[];
  status_breakdown: StatusBreakdownStat[];
};

export type RuleStat = {
  rule_id: string;
  title: string | null;
  severity_default: string | null;
  category_default: string | null;
  finding_count: number;
  blocker_count: number;
  projects_hit: number;
  fp_confirmed: number;
  fp_rejected: number;
  fp_pending: number;
  fp_rate: number;
  resolved_count: number;
};

export type ProjectStat = {
  project_id: string;
  project_name: string;
  review_count: number;
  finding_count: number;
  blocker_count: number;
  fp_confirmed: number;
  avg_duration_ms: number | null;
  last_reviewed_at: string | null;
};

export type CategoryStat = {
  category: string;
  count: number;
  percentage: number;
};

export type TimeseriesPoint = {
  date: string;
  review_count: number;
  finding_count: number;
  blocker_count: number;
};

export async function fetchStatsOverview(days = 30): Promise<StatsOverview> {
  const response = await adminFetch(`/api/stats/overview?days=${days}`);
  return parseJsonResponse<StatsOverview>(response, true);
}

export async function fetchStatsRules(days = 30, limit = 50): Promise<RuleStat[]> {
  const response = await adminFetch(`/api/stats/rules?days=${days}&limit=${limit}`);
  return parseJsonResponse<RuleStat[]>(response, true);
}

export async function fetchStatsProjects(days = 30, limit = 50): Promise<ProjectStat[]> {
  const response = await adminFetch(`/api/stats/projects?days=${days}&limit=${limit}`);
  return parseJsonResponse<ProjectStat[]>(response, true);
}

export async function fetchStatsCategories(days = 30): Promise<CategoryStat[]> {
  const response = await adminFetch(`/api/stats/categories?days=${days}`);
  return parseJsonResponse<CategoryStat[]>(response, true);
}

export async function fetchStatsTimeseries(days = 30): Promise<TimeseriesPoint[]> {
  const response = await adminFetch(`/api/stats/timeseries?days=${days}`);
  return parseJsonResponse<TimeseriesPoint[]>(response, true);
}

// ---------------- 项目通知渠道 API（钉钉推送配置）----------------
//
// 后端按项目维度管理通知渠道（DingTalk Webhook），webhook_url / secret
// 落库加密、响应脱敏为 "****"。渠道 ``enabled=false`` 即暂停推送，
// 渠道为空则该项目不推送。NotificationService 在每次 Review 完成后
// 按启用渠道推送汇总消息（标题 + MR 标题 + Review ID + 问题/阻断数 +
// 详情链接），不推送逐条 finding。

export type NotificationChannelType = 'dingtalk' | 'feishu';

export type NotificationChannel = {
  id: string;
  project_id: string;
  channel_type: NotificationChannelType;
  name: string;
  // 后端响应脱敏为 "****"，前端仅用于展示，不回填表单。
  webhook_url: string;
  secret: string | null;
  enabled: boolean;
  created_at?: string;
  updated_at?: string;
};

export type NotificationChannelFormPayload = {
  channel_type: NotificationChannelType;
  name: string;
  webhook_url: string;
  secret?: string | null;
  enabled: boolean;
};

export type NotificationChannelUpdatePayload = Partial<NotificationChannelFormPayload>;

export async function fetchNotificationChannels(
  projectId: string,
): Promise<NotificationChannel[]> {
  const response = await adminFetch(
    `/api/projects/${projectId}/notification-channels`,
  );
  return parseJsonResponse<NotificationChannel[]>(response, true);
}

export async function createNotificationChannel(
  projectId: string,
  payload: NotificationChannelFormPayload,
): Promise<NotificationChannel> {
  const response = await adminFetch(
    `/api/projects/${projectId}/notification-channels`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return parseJsonResponse<NotificationChannel>(response, true);
}

export async function updateNotificationChannel(
  projectId: string,
  channelId: string,
  payload: NotificationChannelUpdatePayload,
): Promise<NotificationChannel> {
  const response = await adminFetch(
    `/api/projects/${projectId}/notification-channels/${channelId}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return parseJsonResponse<NotificationChannel>(response, true);
}

export async function deleteNotificationChannel(
  projectId: string,
  channelId: string,
): Promise<void> {
  const response = await adminFetch(
    `/api/projects/${projectId}/notification-channels/${channelId}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    throw new Error(`删除通知渠道失败：HTTP ${response.status}`);
  }
}

// ---------------- 用户映射 API（钉钉通知 @MR 创建人）----------------
//
// 后端按项目维度管理 GitLab 用户名 -> 钉钉手机号 / UserID 的映射。
// 钉钉通知在 Review 完成后按 MR 创建人的 GitLab 用户名查映射，
// 命中则在群里 @ 对应的人（手机号或 UserID）。

export type UserMapping = {
  id: string;
  project_id: string;
  gitlab_username: string;
  dingtalk_mobile: string;
  dingtalk_userid: string | null;
  display_name: string | null;
  created_at: string;
  updated_at: string;
};

export type UserMappingCreatePayload = {
  gitlab_username: string;
  dingtalk_mobile: string;
  dingtalk_userid?: string | null;
  display_name?: string | null;
};

export type UserMappingUpdatePayload = {
  gitlab_username?: string;
  dingtalk_mobile?: string;
  dingtalk_userid?: string | null;
  display_name?: string | null;
};

export async function fetchUserMappings(projectId: string): Promise<UserMapping[]> {
  const response = await adminFetch(`/api/projects/${projectId}/user-mappings`);
  return parseJsonResponse<UserMapping[]>(response, true);
}

export async function createUserMapping(
  projectId: string,
  payload: UserMappingCreatePayload,
): Promise<UserMapping> {
  const response = await adminFetch(`/api/projects/${projectId}/user-mappings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<UserMapping>(response, true);
}

export async function updateUserMapping(
  mappingId: string,
  payload: UserMappingUpdatePayload,
): Promise<UserMapping> {
  const response = await adminFetch(`/api/user-mappings/${mappingId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<UserMapping>(response, true);
}

export async function deleteUserMapping(mappingId: string): Promise<void> {
  const response = await adminFetch(`/api/user-mappings/${mappingId}`, { method: 'DELETE' });
  if (!response.ok) {
    throw new Error(`删除用户映射失败：HTTP ${response.status}`);
  }
}

// ---------------- 全局设置 API（全局提示词）----------------

export type GlobalPrompt = {
  content: string;
};

export async function fetchGlobalPrompt(): Promise<GlobalPrompt> {
  const response = await adminFetch('/api/settings/global-prompt');
  return parseJsonResponse<GlobalPrompt>(response, true);
}

export async function updateGlobalPrompt(content: string): Promise<GlobalPrompt> {
  const response = await adminFetch('/api/settings/global-prompt', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  return parseJsonResponse<GlobalPrompt>(response, true);
}

// ---------------- 项目级负样本提示词 API ----------------

export type ProjectNegativePrompt = {
  content: string;
  example_count: number;
};

export type ProjectNegativePromptGenerateResult = {
  content: string;
  source_count: number;
};

export async function fetchProjectNegativePrompt(projectId: string): Promise<ProjectNegativePrompt> {
  const response = await adminFetch(`/api/projects/${projectId}/negative-prompt`);
  return parseJsonResponse<ProjectNegativePrompt>(response, true);
}

export async function updateProjectNegativePrompt(
  projectId: string,
  content: string,
): Promise<ProjectNegativePrompt> {
  const response = await adminFetch(`/api/projects/${projectId}/negative-prompt`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  return parseJsonResponse<ProjectNegativePrompt>(response, true);
}

export async function generateProjectNegativePrompt(
  projectId: string,
): Promise<ProjectNegativePromptGenerateResult> {
  const response = await adminFetch(`/api/projects/${projectId}/negative-prompt/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  return parseJsonResponse<ProjectNegativePromptGenerateResult>(response, true);
}
