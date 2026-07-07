export type HealthStatus = {
  status: string;
  version: string;
  db: string;
  redis: string;
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
  languages: unknown[];
  path_patterns: unknown[];
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
  gitlab_access_token: string;
  webhook_secret: string;
  engine_id: string | null;
  provider_id: string | null;
  enabled: boolean;
  default_block_severity: string;
  timeout_seconds: number;
  max_files: number;
  ignore_paths: unknown[] | null;
  rules: ProjectRuleConfig[];
  block_policies: BlockPolicy[];
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
  created_at?: string;
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
  fp_marked_by?: string | null;
  fp_marked_reason?: string | null;
  fp_reviewed_by?: string | null;
  fp_review_note?: string | null;
  created_at?: string;
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
  gitlab_access_token: string;
  webhook_secret: string;
  engine_id: string;
  provider_id: string;
  enabled: boolean;
  timeout_seconds: number;
  max_files: number;
  default_block_severity: 'INFO' | 'WARNING' | 'BLOCKER';
  rules: ProjectRuleFormPayload[];
};

export type ProjectUpdatePayload = {
  name?: string;
  gitlab_project_id?: string;
  enabled?: boolean;
  default_block_severity?: 'INFO' | 'WARNING' | 'BLOCKER';
  engine_id?: string | null;
  provider_id?: string | null;
  rules?: ProjectRuleFormPayload[];
  block_policies?: BlockPolicyPayload[];
};

export type FalsePositiveMarkPayload = {
  marked_by: string;
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
};

const ADMIN_TOKEN_STORAGE_KEY = 'aicr_admin_access_token';

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

export async function fetchRecentReviews(internalToken: string): Promise<RecentReview[]> {
  const response = await fetch('/api/reviews/recent', {
    headers: {
      'X-Internal-Token': internalToken,
    },
  });
  return parseJsonResponse<RecentReview[]>(response);
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

export async function fetchRules(): Promise<Page<RuleConfig>> {
  const response = await adminFetch('/api/rules');
  return parseJsonResponse<Page<RuleConfig>>(response, true);
}

export async function createRule(payload: RuleFormPayload): Promise<RuleConfig> {
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
    default_block_severity: payload.default_block_severity,
    rules: payload.rules,
  };
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
