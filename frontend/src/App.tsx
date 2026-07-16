import { FormEvent, DragEvent, useEffect, useId, useMemo, useState } from 'react';

import {
  BlockPolicy,
  BlockPolicyPayload,
  BlockPolicySeverity,
  CreateReviewPayload,
  CreateReviewResponse,
  EngineConfig,
  EngineSummary,
  FindingRecord,
  HealthStatus,
  NegativeExample,
  Page,
  ProjectConfig,
  ProjectFormPayload,
  ProjectRuleFormPayload,
  ProjectUpdatePayload,
  ProviderConfig,
  ProviderFormPayload,
  ProviderUpdatePayload,
  RecentReview,
  ReviewRecord,
  RuleConfig,
  RuleCreatePayload,
  RuleFormPayload,
  confirmFalsePositive,
  createProject,
  createProvider,
  createReview,
  createRule,
  deleteProject,
  deleteRule,
  updateProvider,
  updateRule,
  updateProject,
  fetchEngineConfigs,
  fetchEngines,
  fetchFindings,
  fetchHealth,
  fetchNegativeExamples,
  fetchPendingFalsePositives,
  fetchProjects,
  fetchProviders,
  fetchRecentReviews,
  fetchReviewFindings,
  fetchReviewRecords,
  fetchRules,
  clearStoredAdminAccessToken,
  getStoredAdminAccessToken,
  isAuthRequiredError,
  loginAdmin,
  markFalsePositive,
  rejectFalsePositive,
} from './api';
import { AlertOctagon, AlertTriangle, Filter, ScrollText, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { AppShell } from './components/layout/AppShell';
import { Badge as UiBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { LoginPage } from './pages/LoginPage';

type PageKey =
  | 'dashboard'
  | 'providers'
  | 'rules'
  | 'projects'
  | 'reviews'
  | 'findings'
  | 'falsePositives'
  | 'engines';

type FormState = {
  internalToken: string;
  projectId: string;
  mrIid: string;
  targetBranch: string;
  sourceBranch: string;
  commitSha: string;
  projectPath: string;
  title: string;
  webUrl: string;
};

const initialForm: FormState = {
  internalToken: '',
  projectId: '',
  mrIid: '',
  targetBranch: '',
  sourceBranch: '',
  commitSha: '',
  projectPath: '',
  title: '',
  webUrl: '',
};

type LoginFormState = {
  username: string;
  password: string;
};

const initialLoginForm: LoginFormState = {
  username: '',
  password: '',
};

const initialProviderForm: ProviderFormPayload = {
  name: '',
  protocol: 'openai_compatible',
  base_url: '',
  api_key: '',
  model: '',
  temperature: 0,
  max_tokens: 4096,
  enabled: true,
};

const initialProjectForm: ProjectFormPayload = {
  name: '',
  gitlab_project_id: '',
  gitlab_access_token: '',
  webhook_secret: '',
  engine_id: '',
  provider_id: '',
  enabled: true,
  timeout_seconds: 300,
  max_files: 50,
  default_block_severity: 'BLOCKER',
  rules: [],
};

const initialRuleForm: RuleFormPayload = {
  rule_id: '',
  title: '',
  prompt_snippet: '',
  severity_default: 'WARNING',
  enabled: true,
};

const BLOCK_SEVERITY_OPTIONS = ['INFO', 'WARNING', 'BLOCKER'] as const;
const BLOCK_POLICY_SEVERITY_OPTIONS: BlockPolicySeverity[] = [
  'NONE',
  'INFO',
  'WARNING',
  'BLOCKER',
];

const navItems: Array<{ key: PageKey; label: string }> = [
  { key: 'dashboard', label: '仪表盘' },
  { key: 'providers', label: '模型供应商' },
  { key: 'rules', label: '审查规则' },
  { key: 'projects', label: 'GitLab 项目' },
  { key: 'reviews', label: '审查记录' },
  { key: 'findings', label: '问题与误报' },
  { key: 'falsePositives', label: '误报队列' },
  { key: 'engines', label: '引擎配置' },
];

function App() {
  const [adminToken, setAdminToken] = useState(() => getStoredAdminAccessToken());
  const [loginForm, setLoginForm] = useState<LoginFormState>(initialLoginForm);
  const [activePage, setActivePage] = useState<PageKey>('dashboard');
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [engines, setEngines] = useState<EngineSummary[]>([]);
  const [reviews, setReviews] = useState<RecentReview[]>([]);
  const [providersPage, setProvidersPage] = useState<Page<ProviderConfig> | null>(null);
  const [rulesPage, setRulesPage] = useState<Page<RuleConfig> | null>(null);
  const [projectsPage, setProjectsPage] = useState<Page<ProjectConfig> | null>(null);
  const [reviewRecordsPage, setReviewRecordsPage] = useState<Page<ReviewRecord> | null>(null);
  const [findingsPage, setFindingsPage] = useState<Page<FindingRecord> | null>(null);
  const [pendingFpPage, setPendingFpPage] = useState<Page<FindingRecord> | null>(null);
  const [negativeExamplesPage, setNegativeExamplesPage] = useState<Page<NegativeExample> | null>(null);
  const [engineConfigsPage, setEngineConfigsPage] = useState<Page<EngineConfig> | null>(null);
  const [form, setForm] = useState<FormState>(initialForm);
  const [providerForm, setProviderForm] = useState<ProviderFormPayload>(initialProviderForm);
  const [projectForm, setProjectForm] = useState<ProjectFormPayload>(initialProjectForm);
  const [ruleForm, setRuleForm] = useState<RuleFormPayload>(initialRuleForm);
  // 每条规则的编辑态：rule.id → 编辑表单数据（null/缺省表示未进入编辑）。
  // 用 Record 而非单值，避免多条规则互相踩。
  const [ruleEdits, setRuleEdits] = useState<Record<string, RuleFormPayload | null>>({});
  const [operator, setOperator] = useState('admin@example.com');
  const [reviewNote, setReviewNote] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitResult, setSubmitResult] = useState<CreateReviewResponse | null>(null);

  useEffect(() => {
    let active = true;

    async function loadDashboard() {
      if (!adminToken) {
        setLoading(false);
        return;
      }
      try {
        setLoading(true);
        setError(null);
        const [nextHealth, nextEngines] = await Promise.all([fetchHealth(), fetchEngines()]);
        if (active) {
          setHealth(nextHealth);
          setEngines(nextEngines);
        }
      } catch (caught) {
        if (active) {
          handleCaughtError(caught);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void loadDashboard();
    return () => {
      active = false;
    };
  }, [adminToken]);

  useEffect(() => {
    if (!adminToken) {
      return;
    }
    void loadPage(activePage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePage, adminToken]);

  const blockerCount = useMemo(
    () => (reviewRecordsPage?.items ?? []).filter((record) => record.has_blocker).length,
    [reviewRecordsPage],
  );
  const totalFindings = useMemo(
    () => (reviewRecordsPage?.items ?? []).reduce((sum, record) => sum + record.finding_count, 0),
    [reviewRecordsPage],
  );

  async function loadPage(page: PageKey) {
    if (!adminToken) {
      return;
    }
    setError(null);
    try {
      if (page === 'providers') {
        setProvidersPage(await fetchProviders());
      } else if (page === 'rules') {
        setRulesPage(await fetchRules());
      } else if (page === 'projects') {
        const [projects, engineConfigs, rules] = await Promise.all([
          fetchProjects(),
          fetchEngineConfigs(),
          fetchRules(),
        ]);
        setProjectsPage(projects);
        setEngineConfigsPage(engineConfigs);
        setRulesPage(rules);
        // 供应商下拉为可选项：尽力加载一次，失败时退化为仅"默认"项，不阻断项目页加载。
        if (!providersPage) {
          void fetchProviders()
            .then((nextProviders) => setProvidersPage(nextProviders))
            .catch(() => {});
        }
      } else if (page === 'reviews') {
        setReviewRecordsPage(await fetchReviewRecords());
      } else if (page === 'findings') {
        const [allFindings, negativeExamples] = await Promise.all([
          fetchFindings(),
          fetchNegativeExamples(),
        ]);
        setFindingsPage(allFindings);
        setNegativeExamplesPage(negativeExamples);
      } else if (page === 'falsePositives') {
        setPendingFpPage(await fetchPendingFalsePositives());
      } else if (page === 'dashboard') {
        const [records, pendingFp] = await Promise.all([
          fetchReviewRecords(),
          fetchPendingFalsePositives(),
        ]);
        setReviewRecordsPage(records);
        setPendingFpPage(pendingFp);
      } else if (page === 'engines') {
        setEngineConfigsPage(await fetchEngineConfigs());
      }
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      const username = loginForm.username.trim();
      const password = loginForm.password;
      if (!username || !password) {
        throw new Error('管理员账号和密码不能为空。');
      }
      const result = await loginAdmin(username, password);
      setAdminToken(result.access_token);
      setLoginForm(initialLoginForm);
      setMessage('管理台已登录。');
    } catch (caught) {
      handleCaughtError(caught);
    } finally {
      setSubmitting(false);
    }
  }

  function handleLogout() {
    clearStoredAdminAccessToken();
    setAdminToken('');
    setMessage(null);
    setError(null);
    setActivePage('dashboard');
  }

  function handleCaughtError(caught: unknown) {
    if (isAuthRequiredError(caught)) {
      clearStoredAdminAccessToken();
      setAdminToken('');
    }
    setError(toErrorMessage(caught));
  }

  async function handleRefreshReviews() {
    setError(null);
    try {
      const token = form.internalToken.trim();
      if (!token) {
        throw new Error('内部调用 Token 不能为空。');
      }
      setReviews(await fetchRecentReviews(token));
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setSubmitResult(null);
    setError(null);

    try {
      const payload: CreateReviewPayload = {
        project_id: parsePositiveInteger(form.projectId, 'GitLab 项目 ID'),
        mr_iid: parsePositiveInteger(form.mrIid, 'MR IID'),
        target_branch: form.targetBranch.trim(),
        source_branch: form.sourceBranch.trim(),
        commit_sha: form.commitSha.trim(),
      };
      if (form.projectPath.trim()) {
        payload.project_path = form.projectPath.trim();
      }
      if (form.title.trim()) {
        payload.title = form.title.trim();
      }
      if (form.webUrl.trim()) {
        payload.web_url = form.webUrl.trim();
      }
      if (!form.internalToken.trim()) {
        throw new Error('内部调用 Token 不能为空。');
      }
      if (!payload.target_branch || !payload.source_branch || !payload.commit_sha) {
        throw new Error('目标分支、源分支和 Commit SHA 不能为空。');
      }

      const result = await createReview(payload, form.internalToken.trim());
      setSubmitResult(result);
      setReviews(await fetchRecentReviews(form.internalToken.trim()));
    } catch (caught) {
      handleCaughtError(caught);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCreateProvider(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    try {
      await createProvider(providerForm);
      setProviderForm(initialProviderForm);
      setProvidersPage(await fetchProviders());
      setMessage('模型供应商已创建。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleSaveProviderEdit(providerId: string, payload: ProviderUpdatePayload) {
    setError(null);
    setMessage(null);
    try {
      const updated = await updateProvider(providerId, payload);
      setProvidersPage((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((provider) =>
                provider.id === updated.id ? updated : provider,
              ),
            }
          : prev,
      );
      setMessage('模型供应商已更新。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    try {
      await createProject(projectForm);
      setProjectForm(initialProjectForm);
      setProjectsPage(await fetchProjects());
      setMessage('GitLab 项目已创建。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleCreateRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    try {
      if (!ruleForm.title.trim() || !ruleForm.prompt_snippet.trim()) {
        throw new Error('规则标题和提示片段不能为空。');
      }
      // rule_id 可选：留空则不发送该字段，由后端从标题自动生成 slug。
      const payload: RuleCreatePayload = {
        ...ruleForm,
        rule_id: ruleForm.rule_id.trim() || undefined,
      };
      await createRule(payload);
      setRuleForm(initialRuleForm);
      setRulesPage(await fetchRules());
      setMessage('审查规则已创建。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  function startEditRule(rule: RuleConfig) {
    setRuleEdits((prev) => ({
      ...prev,
      [rule.id]: {
        rule_id: rule.rule_id,
        title: rule.title,
        prompt_snippet: rule.prompt_snippet,
        severity_default: rule.severity_default as RuleFormPayload['severity_default'],
        enabled: rule.enabled,
      },
    }));
  }

  function cancelEditRule(id: string) {
    setRuleEdits((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  function updateEditRule(id: string, patch: Partial<RuleFormPayload>) {
    setRuleEdits((prev) => {
      const current = prev[id];
      if (!current) {
        return prev;
      }
      return { ...prev, [id]: { ...current, ...patch } };
    });
  }

  async function handleUpdateRule(id: string) {
    const form = ruleEdits[id];
    if (!form) {
      return;
    }
    setError(null);
    setMessage(null);
    try {
      if (!form.title.trim() || !form.prompt_snippet.trim()) {
        throw new Error('规则标题和提示片段不能为空。');
      }
      // rule_id 只读，不随更新提交，避免误改业务标识。
      await updateRule(id, {
        title: form.title,
        prompt_snippet: form.prompt_snippet,
        severity_default: form.severity_default,
        enabled: form.enabled,
      });
      setRuleEdits((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      setRulesPage(await fetchRules());
      setMessage('审查规则已更新。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleDeleteRule(rule: RuleConfig) {
    if (!window.confirm(`确定删除规则「${rule.rule_id}」？该操作不可撤销。`)) {
      return;
    }
    setError(null);
    setMessage(null);
    try {
      await deleteRule(rule.id);
      setRulesPage(await fetchRules());
      setMessage('审查规则已删除。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleSaveBlockPolicies(projectId: string, policies: BlockPolicyPayload[]) {
    setError(null);
    setMessage(null);
    try {
      if (policies.some((policy) => !policy.branch_pattern.trim())) {
        throw new Error('每条策略的分支匹配不能为空。');
      }
      const updated = await updateProject(projectId, { block_policies: policies });
      setProjectsPage((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((project) => (project.id === updated.id ? updated : project)),
            }
          : prev,
      );
      setMessage('阻断策略已保存。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleSaveProjectEdit(projectId: string, payload: ProjectUpdatePayload) {
    setError(null);
    setMessage(null);
    try {
      const updated = await updateProject(projectId, payload);
      setProjectsPage((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((project) => (project.id === updated.id ? updated : project)),
            }
          : prev,
      );
      setMessage('GitLab 项目已更新。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  // Issue #73：删除项目后本地过滤列表刷新，参考 handleDeleteRule 的模式。
  async function handleDeleteProject(id: string) {
    setError(null);
    setMessage(null);
    try {
      await deleteProject(id);
      setProjectsPage((prev) =>
        prev ? { ...prev, items: prev.items.filter((project) => project.id !== id), total: Math.max(prev.total - 1, 0) } : prev,
      );
      setMessage('GitLab 项目已删除。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleMarkFalsePositive(finding: FindingRecord) {
    setError(null);
    setMessage(null);
    try {
      await markFalsePositive(finding.id, { marked_by: operator, reason: reviewNote || 'MVP 管理台标记' });
      setFindingsPage(await fetchFindings());
      setMessage('问题已标记为待确认误报。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleReviewFalsePositive(finding: FindingRecord, action: 'confirm' | 'reject') {
    setError(null);
    setMessage(null);
    try {
      const payload = { reviewed_by: operator, note: reviewNote || undefined };
      if (action === 'confirm') {
        await confirmFalsePositive(finding.id, payload);
      } else {
        await rejectFalsePositive(finding.id, payload);
      }
      const [pending, negativeExamples] = await Promise.all([
        fetchPendingFalsePositives(),
        fetchNegativeExamples(),
      ]);
      setPendingFpPage(pending);
      setNegativeExamplesPage(negativeExamples);
      setMessage(action === 'confirm' ? '误报已确认并沉淀为负例。' : '误报申请已驳回。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  if (!adminToken) {
    return (
      <LoginPage
        form={loginForm}
        onChange={(patch) => setLoginForm({ ...loginForm, ...patch })}
        onSubmit={handleLogin}
        submitting={submitting}
        error={error}
        message={message}
      />
    );
  }

  return (
    <AppShell activePage={activePage} onNavigate={setActivePage} health={health} onLogout={handleLogout}>
      {error ? <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{error}</div> : null}
      {message ? <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm text-primary">{message}</div> : null}
      {activePage === 'dashboard' ? renderDashboard() : null}
      {activePage === 'providers' ? renderProviders() : null}
      {activePage === 'rules' ? renderRules() : null}
      {activePage === 'projects' ? renderProjects() : null}
      {activePage === 'reviews' ? renderReviewRecords() : null}
      {activePage === 'findings' ? renderFindings() : null}
      {activePage === 'falsePositives' ? renderFalsePositives() : null}
      {activePage === 'engines' ? renderEngineConfigs() : null}
    </AppShell>
  );

  function renderDashboard() {
    return (
      <div className="space-y-6" aria-busy={loading}>
        {/* KPI 行 */}
        <section>
          <div className="grid grid-cols-4 gap-3">
            <KpiCard label="总审查数" value={reviewRecordsPage?.total ?? '—'} icon={ScrollText} />
            <KpiCard
              label="阻断问题"
              value={blockerCount}
              icon={AlertOctagon}
              intent={blockerCount > 0 ? 'danger' : 'neutral'}
            />
            <KpiCard label="发现问题" value={totalFindings} icon={AlertTriangle} />
            <KpiCard label="待处理误报" value={pendingFpPage?.total ?? 0} icon={Filter} hint="待处理" />
          </div>
        </section>

        {/* 系统状态 + 最近审查 */}
        <section className="grid grid-cols-3 gap-4">
          <SystemStatusCard health={health} engines={engines} />
          <RecentReviewsPanel reviews={reviews} onViewAll={() => setActivePage('reviews')} />
        </section>

        {/* 手动触发 MR 审查 */}
        <section>
          <Card>
            <CardHeader>
              <div>
                <CardTitle>手动触发 MR 审查</CardTitle>
                <CardDescription>填写 GitLab MR 参数触发一次审查</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <form className="grid grid-cols-2 gap-3" onSubmit={handleSubmit}>
                <TextInput label="内部调用 Token" type="password" value={form.internalToken} onChange={(value) => setForm({ ...form, internalToken: value })} />
                <TextInput label="GitLab 项目 ID" value={form.projectId} onChange={(value) => setForm({ ...form, projectId: value })} />
                <TextInput label="MR IID" value={form.mrIid} onChange={(value) => setForm({ ...form, mrIid: value })} />
                <TextInput label="目标分支" value={form.targetBranch} onChange={(value) => setForm({ ...form, targetBranch: value })} />
                <TextInput label="源分支" value={form.sourceBranch} onChange={(value) => setForm({ ...form, sourceBranch: value })} />
                <TextInput label="Commit SHA" value={form.commitSha} onChange={(value) => setForm({ ...form, commitSha: value })} />
                <TextInput label="项目路径（可选）" value={form.projectPath} onChange={(value) => setForm({ ...form, projectPath: value })} />
                <TextInput label="MR 标题（可选）" value={form.title} onChange={(value) => setForm({ ...form, title: value })} />
                <TextInput label="MR URL（可选）" value={form.webUrl} onChange={(value) => setForm({ ...form, webUrl: value })} />
                <div className="col-span-2 mt-4 flex items-center justify-between border-t border-zinc-100 pt-4">
                  <span className="text-[12px] text-zinc-500">Token 只在本次请求中使用，不会保存到前端状态之外</span>
                  <div className="flex items-center gap-2">
                    <Button variant="secondary" type="button" disabled={submitting} onClick={handleRefreshReviews}>刷新最近审查</Button>
                    <Button type="submit" disabled={submitting}>{submitting ? '审查中…' : '触发审查'}</Button>
                  </div>
                </div>
              </form>
              {submitResult ? (
                <div className="flex items-center gap-2">
                  <UiBadge variant={submitResult.has_blocker ? 'destructive' : 'success'}>
                    {submitResult.has_blocker ? '审查完成，发现阻断问题。' : '审查完成，未发现阻断问题。'}
                  </UiBadge>
                  {submitResult.review_url ? (
                    <a href={submitResult.review_url} className="text-[12px] font-medium text-brand hover:underline">查看结果 →</a>
                  ) : null}
                </div>
              ) : null}
            </CardContent>
          </Card>
        </section>
      </div>
    );
  }

  function renderProviders() {
    const items = providersPage?.items ?? [];
    const enabledCount = items.filter((provider) => provider.enabled).length;
    return (
      <div className="grid grid-cols-5 gap-4">
        {/* 左：表单卡 */}
        <Card className="col-span-2">
          <CardHeader>
            <div>
              <CardTitle>新增供应商</CardTitle>
              <CardDescription>目前支持 OpenAI 兼容、Anthropic、Custom 协议</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <form onSubmit={handleCreateProvider} className="space-y-3">
              <TextInput label="名称" value={providerForm.name} onChange={(value) => setProviderForm({ ...providerForm, name: value })} />
              <SelectInput label="协议" value={providerForm.protocol} options={['openai_compatible', 'anthropic', 'custom']} onChange={(value) => setProviderForm({ ...providerForm, protocol: value as ProviderFormPayload['protocol'] })} />
              <TextInput label="Base URL" value={providerForm.base_url} onChange={(value) => setProviderForm({ ...providerForm, base_url: value })} />
              <TextInput label="API Key" type="password" value={providerForm.api_key} onChange={(value) => setProviderForm({ ...providerForm, api_key: value })} />
              <div className="grid grid-cols-2 gap-3">
                <TextInput label="模型" value={providerForm.model} onChange={(value) => setProviderForm({ ...providerForm, model: value })} />
                <TextInput label="Max Tokens" value={String(providerForm.max_tokens)} onChange={(value) => setProviderForm({ ...providerForm, max_tokens: Number(value) || 0 })} />
              </div>
              <div className="pt-3 flex items-center justify-end gap-2 border-t border-zinc-100 mt-4">
                <Button type="button" variant="secondary" onClick={() => setProviderForm(initialProviderForm)}>重置</Button>
                <Button type="submit">保存供应商</Button>
              </div>
            </form>
          </CardContent>
        </Card>

        {/* 右：列表卡 */}
        <Card className="col-span-3">
          <CardHeader>
            <div>
              <CardTitle>已配置供应商</CardTitle>
              <CardDescription>{items.length} 个，其中 {enabledCount} 个已启用</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <div className="p-6 text-center text-[13px] text-zinc-500">暂无模型供应商</div>
            ) : (
              items.map((provider) => (
                <ProviderListItem
                  key={provider.id}
                  provider={provider}
                  onSave={handleSaveProviderEdit}
                />
              ))
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  function renderRules() {
    const items = rulesPage?.items ?? [];
    const enabledCount = items.filter((rule) => rule.enabled).length;
    return (
      <div className="grid grid-cols-5 gap-4">
        {/* 左：表单卡 */}
        <Card className="col-span-2">
          <CardHeader>
            <div>
              <CardTitle>新增审查规则</CardTitle>
              <CardDescription>定义 AI 审查依据的规则模板</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <form className="space-y-3" onSubmit={handleCreateRule}>
              <TextInput label="规则 ID" hint="可选：留空则自动从标题生成" value={ruleForm.rule_id} onChange={(value) => setRuleForm({ ...ruleForm, rule_id: value })} />
              <TextInput label="标题" value={ruleForm.title} onChange={(value) => setRuleForm({ ...ruleForm, title: value })} />
              <TextAreaInput label="提示片段" value={ruleForm.prompt_snippet} onChange={(value) => setRuleForm({ ...ruleForm, prompt_snippet: value })} />
              <SelectInput label="默认严重级别" value={ruleForm.severity_default} options={BLOCK_SEVERITY_OPTIONS} onChange={(value) => setRuleForm({ ...ruleForm, severity_default: value as RuleFormPayload['severity_default'] })} />
              <CheckboxInput label="启用规则" checked={ruleForm.enabled} onChange={(value) => setRuleForm({ ...ruleForm, enabled: value })} />
              <div className="pt-3 flex items-center justify-end gap-2 border-t border-zinc-100 mt-4">
                <Button type="button" variant="secondary" onClick={() => setRuleForm(initialRuleForm)}>重置</Button>
                <Button type="submit">保存规则</Button>
              </div>
            </form>
          </CardContent>
        </Card>

        {/* 右：列表卡 */}
        <Card className="col-span-3">
          <CardHeader>
            <div>
              <CardTitle>审查规则</CardTitle>
              <CardDescription>{items.length} 条规则 · {enabledCount} 启用</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <div className="p-6 text-center text-[13px] text-zinc-500">暂无审查规则</div>
            ) : (
              items.map((rule) => {
                const editForm = ruleEdits[rule.id] ?? null;
                return (
                  <div key={rule.id} className="border-b border-zinc-100 last:border-b-0">
                    <div className="flex items-center justify-between px-4 py-3 hover:bg-zinc-50 transition-colors">
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] font-medium text-zinc-900 truncate">
                          <span className="font-mono">{rule.rule_id}</span><span className="font-normal text-zinc-600"> {rule.title}</span>
                        </div>
                        <div className="text-[11px] text-zinc-500 mt-0.5 font-mono truncate">{rule.severity_default} · {truncate(rule.prompt_snippet, 60)}</div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <UiBadge {...severityBadgeProps(rule.severity_default)}>{rule.severity_default}</UiBadge>
                        <UiBadge variant={rule.enabled ? 'success' : 'default'}>
                          <span className={cn('w-1.5 h-1.5 rounded-full', rule.enabled ? 'bg-emerald-500' : 'bg-zinc-400')} />
                          {rule.enabled ? '启用' : '停用'}
                        </UiBadge>
                        <Button variant="ghost" size="sm" type="button" onClick={() => startEditRule(rule)}>编辑</Button>
                        <Button variant="destructive" size="sm" type="button" onClick={() => void handleDeleteRule(rule)}>删除</Button>
                      </div>
                    </div>
                    {editForm ? (
                      <div className="space-y-3 border-t border-zinc-100 bg-zinc-50 px-4 pb-4 pt-3">
                        <div className="space-y-1.5">
                          <span className="block text-[12px] font-medium text-zinc-600">规则 ID（只读）</span>
                          <div className="flex h-9 w-full items-center rounded-md border border-input bg-zinc-100 px-3 py-1 font-mono text-[13px] text-zinc-500">
                            {editForm.rule_id}
                          </div>
                        </div>
                        <TextInput label="标题" value={editForm.title} onChange={(value) => updateEditRule(rule.id, { title: value })} />
                        <TextAreaInput label="提示片段" value={editForm.prompt_snippet} onChange={(value) => updateEditRule(rule.id, { prompt_snippet: value })} />
                        <SelectInput label="默认严重级别" value={editForm.severity_default} options={BLOCK_SEVERITY_OPTIONS} onChange={(value) => updateEditRule(rule.id, { severity_default: value as RuleFormPayload['severity_default'] })} />
                        <CheckboxInput label="启用规则" checked={editForm.enabled} onChange={(value) => updateEditRule(rule.id, { enabled: value })} />
                        <div className="flex items-center justify-end gap-2 pt-1">
                          <Button variant="secondary" size="sm" type="button" onClick={() => cancelEditRule(rule.id)}>取消</Button>
                          <Button size="sm" type="button" onClick={() => void handleUpdateRule(rule.id)}>保存</Button>
                        </div>
                      </div>
                    ) : null}
                  </div>
                );
              })
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  function renderProjects() {
    const engineOptions: SelectOption[] = [
      { value: '', label: '不指定' },
      ...(engineConfigsPage?.items ?? []).map((engine) => ({ value: engine.id, label: engine.name })),
    ];
    const providerOptions: SelectOption[] = [
      { value: '', label: '（不选择，使用默认）' },
      ...(providersPage?.items ?? []).map((provider) => ({ value: provider.id, label: provider.name })),
    ];
    return (
      <div className="grid grid-cols-5 gap-4">
        {/* 左：表单卡 col-span-2 */}
        <Card className="col-span-2">
          <CardHeader>
            <div>
              <CardTitle>新增 GitLab 项目</CardTitle>
              <CardDescription>接入项目后可通过 Webhook 自动触发 MR 审查</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <form onSubmit={handleCreateProject} className="space-y-3">
              <TextInput label="项目名称" value={projectForm.name} onChange={(value) => setProjectForm({ ...projectForm, name: value })} />
              <TextInput label="GitLab Project ID" value={projectForm.gitlab_project_id} onChange={(value) => setProjectForm({ ...projectForm, gitlab_project_id: value })} />
              <TextInput label="GitLab Access Token" type="password" value={projectForm.gitlab_access_token} onChange={(value) => setProjectForm({ ...projectForm, gitlab_access_token: value })} />
              <TextInput label="Webhook Secret" type="password" value={projectForm.webhook_secret} onChange={(value) => setProjectForm({ ...projectForm, webhook_secret: value })} />
              <SelectInput label="默认审查引擎" value={projectForm.engine_id} options={engineOptions} onChange={(value) => setProjectForm({ ...projectForm, engine_id: value })} />
              <SelectInput label="AI 供应商" value={projectForm.provider_id} options={providerOptions} onChange={(value) => setProjectForm({ ...projectForm, provider_id: value })} />
              <div className="grid grid-cols-2 gap-3">
                <TextInput label="超时秒数" value={String(projectForm.timeout_seconds)} onChange={(value) => setProjectForm({ ...projectForm, timeout_seconds: Number(value) || 0 })} />
                <TextInput label="最大文件数" value={String(projectForm.max_files)} onChange={(value) => setProjectForm({ ...projectForm, max_files: Number(value) || 0 })} />
              </div>
              <SelectInput label="默认阻断级别" value={projectForm.default_block_severity} options={BLOCK_SEVERITY_OPTIONS} onChange={(value) => setProjectForm({ ...projectForm, default_block_severity: value as ProjectFormPayload['default_block_severity'] })} />
              <div>
                <label className="text-[12px] font-medium text-zinc-600 mb-2 block">启用规则</label>
                <div className="rounded-md border border-zinc-200 max-h-48 overflow-y-auto">
                  {(rulesPage?.items ?? []).length === 0 ? (
                    <div className="p-3 text-[12px] text-zinc-500">暂无规则，请先到"审查规则"页面创建。</div>
                  ) : (
                    (rulesPage?.items ?? []).map((rule) => (
                      <label key={rule.id} className="flex items-center gap-2 px-3 py-2 border-b border-zinc-100 last:border-b-0 text-[13px] cursor-pointer hover:bg-zinc-50">
                        <input
                          type="checkbox"
                          className="size-4 rounded border-zinc-300 accent-indigo-600"
                          checked={projectForm.rules.some((selected) => selected.rule_id === rule.id)}
                          onChange={(event) => setProjectForm((prev) => ({ ...prev, rules: toggleRuleSelection(prev.rules, rule.id, event.target.checked) }))}
                        />
                        <span className="text-zinc-900">{rule.rule_id}</span>
                        <span className="text-zinc-500">·</span>
                        <span className="text-zinc-600 truncate">{rule.title}</span>
                      </label>
                    ))
                  )}
                </div>
              </div>
              <div className="pt-3 flex items-center justify-end gap-2 border-t border-zinc-100 mt-4">
                <Button type="button" variant="secondary" onClick={() => setProjectForm(initialProjectForm)}>重置</Button>
                <Button type="submit">保存项目</Button>
              </div>
            </form>
          </CardContent>
        </Card>

        {/* 右：项目列表 col-span-3 */}
        <Card className="col-span-3">
          <CardHeader>
            <div>
              <CardTitle>已接入项目</CardTitle>
              <CardDescription>{projectsPage?.items?.length ?? 0} 个项目 · {(projectsPage?.items ?? []).filter((p) => p.enabled).length} 启用</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {(projectsPage?.items ?? []).length === 0 ? (
              <div className="p-6 text-center text-[13px] text-zinc-500">暂无 GitLab 项目</div>
            ) : (
              (projectsPage?.items ?? []).map((project) => (
                <ProjectCard
                  key={project.id}
                  project={project}
                  providerOptions={providerOptions}
                  rules={rulesPage?.items ?? []}
                  onSavePolicies={handleSaveBlockPolicies}
                  onSaveProject={handleSaveProjectEdit}
                  onDeleteProject={handleDeleteProject}
                />
              ))
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  function renderReviewRecords() {
    const items = reviewRecordsPage?.items ?? [];
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>审查记录</CardTitle>
              <CardDescription>{items.length} 条历史审查</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <div className="p-6 text-center text-[13px] text-zinc-500">暂无审查记录</div>
            ) : (
              items.map((review) => (
                <ReviewRecordRow key={review.id} review={review} onError={handleCaughtError} />
              ))
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  function renderFindings() {
    const items = findingsPage?.items ?? [];
    const negativeExamples = negativeExamplesPage?.items ?? [];
    return (
      <div className="space-y-4">
        <Card>
          <CardContent className="p-4">
            <div className="grid grid-cols-2 gap-3">
              <TextInput label="操作人" value={operator} onChange={setOperator} />
              <TextInput label="处理说明" value={reviewNote} onChange={setReviewNote} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>问题与误报</CardTitle>
              <CardDescription>{items.length} 条问题记录</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <div className="p-6 text-center text-[13px] text-zinc-500">暂无问题记录</div>
            ) : (
              items.map((finding) => (
                <div key={finding.id} className="flex items-center justify-between px-4 py-3 border-b border-zinc-100 last:border-b-0 hover:bg-zinc-50 transition-colors">
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-zinc-900 truncate">{finding.title}</div>
                    <div className="text-[11px] text-zinc-500 mt-0.5 font-mono truncate">{finding.file_path}:{finding.line_number ?? '-'} · {finding.rule_id} · {finding.fp_status}</div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <UiBadge {...severityBadgeProps(finding.severity)}>{finding.severity}</UiBadge>
                    <Button variant="ghost" size="sm" type="button" onClick={() => void handleMarkFalsePositive(finding)}>标记误报</Button>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {negativeExamples.length > 0 ? (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>负样本</CardTitle>
                <CardDescription>{negativeExamples.length} 条已批准</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              {negativeExamples.map((example) => (
                <div key={example.id} className="px-4 py-3 border-b border-zinc-100 last:border-b-0">
                  <div className="text-[12px] font-mono bg-zinc-50 rounded p-2 mb-2 whitespace-pre-wrap break-all">
                    {example.code_snippet}
                  </div>
                  {example.explanation ? <div className="text-[12px] text-zinc-600">{example.explanation}</div> : null}
                </div>
              ))}
            </CardContent>
          </Card>
        ) : null}
      </div>
    );
  }

  function renderFalsePositives() {
    const items = pendingFpPage?.items ?? [];
    const negativeExamples = negativeExamplesPage?.items ?? [];
    return (
      <div className="space-y-4">
        <Card>
          <CardContent className="p-4">
            <div className="grid grid-cols-2 gap-3">
              <TextInput label="审核人" value={operator} onChange={setOperator} />
              <TextInput label="审核备注" value={reviewNote} onChange={setReviewNote} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>误报队列</CardTitle>
              <CardDescription>{items.length} 条待审核</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <div className="p-6 text-center text-[13px] text-zinc-500">暂无待确认误报</div>
            ) : (
              items.map((finding) => (
                <div key={finding.id} className="flex items-center justify-between px-4 py-3 border-b border-zinc-100 last:border-b-0 hover:bg-zinc-50 transition-colors">
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-zinc-900 truncate">{finding.title}</div>
                    <div className="text-[11px] text-zinc-500 mt-0.5 font-mono truncate">{finding.file_path}:{finding.line_number ?? '-'} · 提交人 {finding.fp_marked_by ?? '未知'} · {finding.fp_marked_reason ?? '无原因'}</div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Button size="sm" type="button" onClick={() => void handleReviewFalsePositive(finding, 'confirm')}>确认误报</Button>
                    <Button variant="secondary" size="sm" type="button" onClick={() => void handleReviewFalsePositive(finding, 'reject')}>驳回</Button>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {negativeExamples.length > 0 ? (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>负样本</CardTitle>
                <CardDescription>{negativeExamples.length} 条已批准</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              {negativeExamples.map((example) => (
                <div key={example.id} className="px-4 py-3 border-b border-zinc-100 last:border-b-0">
                  <div className="text-[12px] font-mono bg-zinc-50 rounded p-2 mb-2 whitespace-pre-wrap break-all">
                    {example.code_snippet}
                  </div>
                  {example.explanation ? <div className="text-[12px] text-zinc-600">{example.explanation}</div> : null}
                </div>
              ))}
            </CardContent>
          </Card>
        ) : null}
      </div>
    );
  }

  function renderEngineConfigs() {
    const items = engineConfigsPage?.items ?? [];
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>引擎配置</CardTitle>
              <CardDescription>{items.length} 个引擎</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <div className="p-6 text-center text-[13px] text-zinc-500">暂无引擎配置</div>
            ) : (
              items.map((engine) => (
                <div key={engine.id} className="flex items-center justify-between px-4 py-3 border-b border-zinc-100 last:border-b-0 hover:bg-zinc-50 transition-colors">
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-zinc-900 truncate">{engine.name}</div>
                    <div className="text-[11px] text-zinc-500 mt-0.5 truncate">{engine.description ?? '暂无描述'}</div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <UiBadge variant={engine.enabled ? 'success' : 'default'}>
                      <span className={cn('w-1.5 h-1.5 rounded-full', engine.enabled ? 'bg-emerald-500' : 'bg-zinc-400')} />
                      {engine.enabled ? '启用' : '停用'}
                    </UiBadge>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>
    );
  }
}

type TextInputProps = {
  label: string;
  value: string;
  type?: string;
  hint?: string;
  placeholder?: string;
  onChange: (value: string) => void;
};

function TextInput({ label, value, type = 'text', hint, placeholder, onChange }: TextInputProps) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint ? <span className="block text-[11px] text-zinc-500">{hint}</span> : null}
    </div>
  );
}

type SelectOption = string | { value: string; label: string };

type SelectInputProps = {
  label: string;
  value: string;
  options: readonly SelectOption[];
  onChange: (value: string) => void;
};

function SelectInput({ label, value, options, onChange }: SelectInputProps) {
  const id = useId();
  const normalized = options.map((option) =>
    typeof option === 'string' ? { value: option, label: option } : option,
  );
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
      >
        {normalized.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </div>
  );
}

type TextAreaInputProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
};

function TextAreaInput({ label, value, onChange }: TextAreaInputProps) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Textarea id={id} rows={4} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

type CheckboxInputProps = {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
};

function CheckboxInput({ label, checked, onChange }: CheckboxInputProps) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" className="size-4 rounded border-input accent-primary" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

type StatusRowProps = {
  label: string;
  value: string;
  ok: boolean;
};

function StatusRow({ label, value, ok }: StatusRowProps) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border py-2 last:border-b-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <Badge ok={ok}>{value}</Badge>
    </div>
  );
}

type BadgeProps = {
  ok: boolean;
  children: string;
};

function Badge({ ok, children }: BadgeProps) {
  return <UiBadge variant={ok ? 'success' : 'destructive'}>{children}</UiBadge>;
}

type KpiCardProps = {
  label: string;
  value: number | string;
  icon: LucideIcon;
  hint?: string;
  intent?: 'neutral' | 'danger';
};

function KpiCard({ label, value, icon: Icon, hint, intent = 'neutral' }: KpiCardProps) {
  const danger = intent === 'danger' && Number(value) > 0;
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <span className="text-[12px] text-zinc-500">{label}</span>
          <Icon size={14} strokeWidth={1.75} className="text-zinc-400" />
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span
            className={cn(
              'text-[24px] font-semibold leading-none tracking-tight',
              danger ? 'text-rose-600' : 'text-zinc-900',
            )}
          >
            {value}
          </span>
          {hint ? <span className="text-[12px] font-medium text-zinc-500">{hint}</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}

type SystemStatusCardProps = {
  health: HealthStatus | null;
  engines: EngineSummary[];
};

function SystemStatusCard({ health, engines }: SystemStatusCardProps) {
  const apiOk = health?.status === 'ok';
  const dbOk = health?.db === 'ok';
  const redisOk = health?.redis === 'ok';
  const healthyEngineCount = engines.filter((engine) => engine.healthy).length;
  const enginesAllHealthy = engines.length > 0 && healthyEngineCount === engines.length;
  const enginesAllDown = engines.length > 0 && healthyEngineCount === 0;
  const engineDotClass = engines.length === 0
    ? 'bg-zinc-300'
    : enginesAllHealthy
      ? 'bg-emerald-500'
      : enginesAllDown
        ? 'bg-rose-500'
        : 'bg-amber-500';
  const allOk = apiOk && dbOk && redisOk && (engines.length === 0 || enginesAllHealthy);

  const rows: Array<{ label: string; ok: boolean; value: string; version?: string }> = [
    { label: 'API 服务', ok: apiOk, value: apiOk ? '服务正常' : '服务异常', version: health?.version },
    { label: '数据库', ok: dbOk, value: dbOk ? '数据库正常' : '数据库异常' },
    { label: 'Redis', ok: redisOk, value: redisOk ? 'Redis 正常' : 'Redis 异常' },
  ];

  return (
    <Card className="col-span-1">
      <CardHeader>
        <div>
          <CardTitle>系统状态</CardTitle>
          <CardDescription>实时组件健康度</CardDescription>
        </div>
        <UiBadge variant={allOk ? 'success' : 'destructive'}>
          <span className={cn('size-1.5 rounded-full', allOk ? 'bg-emerald-500' : 'bg-rose-500')} />
          {allOk ? '正常' : '异常'}
        </UiBadge>
      </CardHeader>
      <CardContent className="p-0">
        {rows.map((row, index) => (
          <div
            key={row.label}
            className={cn(
              'flex items-center justify-between px-4 py-2',
              index < rows.length - 1 && 'border-b border-zinc-100',
            )}
          >
            <div className="flex items-center gap-2">
              <span className={cn('size-1.5 rounded-full', row.ok ? 'bg-emerald-500' : 'bg-rose-500')} />
              <span className="text-[13px] text-zinc-700">{row.label}</span>
            </div>
            {row.version ? (
              <div className="flex items-baseline gap-2">
                <span className={cn('text-[11px]', row.ok ? 'text-zinc-500' : 'text-rose-600')}>{row.value}</span>
                <span className="font-mono text-[11px] text-zinc-400">v{row.version}</span>
              </div>
            ) : (
              <span className={cn('text-[11px]', row.ok ? 'text-zinc-500' : 'text-rose-600')}>{row.value}</span>
            )}
          </div>
        ))}
        <div className="flex items-center justify-between px-4 py-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className={cn('size-1.5 shrink-0 rounded-full', engineDotClass)} />
            <span className="shrink-0 text-[13px] text-zinc-700">引擎</span>
            <span className="truncate text-[13px] text-zinc-700">
              {engines.length > 0 ? engines.map((engine) => engine.name).join(' / ') : '—'}
            </span>
          </div>
          <span className="ml-2 shrink-0 font-mono text-[11px] text-zinc-500">
            {healthyEngineCount}/{engines.length}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

type ProviderListItemProps = {
  provider: ProviderConfig;
  onSave: (providerId: string, payload: ProviderUpdatePayload) => Promise<void>;
};

function ProviderListItem({ provider, onSave }: ProviderListItemProps) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<ProviderFormPayload>(initialProviderForm);
  const letter = provider.name.charAt(0).toUpperCase();
  const baseUrlShort = provider.base_url.replace(/^https?:\/\//, '').replace(/\/$/, '');

  function startEdit() {
    // api_key 留空表示不修改，避免把脱敏的 **** 误回写。
    setForm({
      name: provider.name,
      protocol: provider.protocol as ProviderFormPayload['protocol'],
      base_url: provider.base_url,
      api_key: '',
      model: provider.model,
      temperature: provider.temperature,
      max_tokens: provider.max_tokens,
      enabled: provider.enabled,
    });
    setEditing(true);
  }

  async function handleSave() {
    if (saving) {
      return;
    }
    const payload: ProviderUpdatePayload = { ...form };
    if (!form.api_key.trim()) {
      delete payload.api_key;
    }
    try {
      setSaving(true);
      await onSave(provider.id, payload);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="border-b border-zinc-100 last:border-b-0">
      <div className="flex items-center justify-between px-4 py-3 hover:bg-zinc-50 transition-colors">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 rounded-md bg-zinc-100 flex items-center justify-center shrink-0">
            <span className="text-[13px] font-semibold text-zinc-700">{letter}</span>
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-medium text-zinc-900 truncate">{provider.name}</span>
            </div>
            <div className="text-[11px] text-zinc-500 mt-0.5 truncate font-mono">
              {provider.protocol} · {provider.model} · {baseUrlShort}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <UiBadge variant={provider.enabled ? 'success' : 'default'}>
            <span className={cn('w-1.5 h-1.5 rounded-full', provider.enabled ? 'bg-emerald-500' : 'bg-zinc-400')} />
            {provider.enabled ? '已启用' : '已停用'}
          </UiBadge>
          <Button variant="ghost" size="sm" type="button" onClick={startEdit}>编辑</Button>
        </div>
      </div>
      {editing ? (
        <div className="space-y-3 border-t border-zinc-100 bg-zinc-50 px-4 pb-4 pt-3">
          <TextInput label="名称" value={form.name} onChange={(value) => setForm({ ...form, name: value })} />
          <SelectInput label="协议" value={form.protocol} options={['openai_compatible', 'anthropic', 'custom']} onChange={(value) => setForm({ ...form, protocol: value as ProviderFormPayload['protocol'] })} />
          <TextInput label="Base URL" value={form.base_url} onChange={(value) => setForm({ ...form, base_url: value })} />
          <TextInput label="API Key" type="password" placeholder="留空则不修改" value={form.api_key} onChange={(value) => setForm({ ...form, api_key: value })} />
          <div className="grid grid-cols-2 gap-3">
            <TextInput label="模型" value={form.model} onChange={(value) => setForm({ ...form, model: value })} />
            <TextInput label="Max Tokens" value={String(form.max_tokens)} onChange={(value) => setForm({ ...form, max_tokens: Number(value) || 0 })} />
          </div>
          <CheckboxInput label="启用供应商" checked={form.enabled} onChange={(value) => setForm({ ...form, enabled: value })} />
          <div className="flex items-center justify-end gap-2 pt-1">
            <Button variant="secondary" size="sm" type="button" disabled={saving} onClick={() => setEditing(false)}>取消</Button>
            <Button size="sm" type="button" disabled={saving} onClick={() => void handleSave()}>{saving ? '保存中…' : '保存'}</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

type RecentReviewsPanelProps = {
  reviews: RecentReview[];
  onViewAll: () => void;
};

function RecentReviewsPanel({ reviews, onViewAll }: RecentReviewsPanelProps) {
  const items = reviews.slice(0, 5);
  return (
    <Card className="col-span-2">
      <CardHeader>
        <div>
          <CardTitle>最近审查</CardTitle>
          <CardDescription>最近 5 条 MR 审查记录</CardDescription>
        </div>
        <button
          type="button"
          onClick={onViewAll}
          className="text-[12px] font-medium text-zinc-500 transition-colors hover:text-zinc-900"
        >
          查看全部 →
        </button>
      </CardHeader>
      {items.length === 0 ? (
        <div className="p-6 text-center text-[13px] text-zinc-500">暂无审查记录</div>
      ) : (
        <div>
          {items.map((review) => (
            <div
              key={`${review.review_id ?? review.project_id}-${review.mr_iid}`}
              className="flex items-center gap-3 border-b border-zinc-100 px-4 py-2.5 last:border-b-0 hover:bg-[#FAFAFA]"
            >
              <span
                className={cn(
                  'size-1.5 shrink-0 rounded-full',
                  review.status === 'engine_error'
                    ? review.has_blocker
                      ? 'bg-rose-500'
                      : 'bg-amber-500'
                    : review.has_blocker
                      ? 'bg-rose-500'
                      : 'bg-emerald-500',
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[13px] font-medium text-zinc-900">
                    {review.title || `MR !${review.mr_iid}`}
                  </span>
                  <UiBadge variant="default" className="h-4 px-1.5 text-[10px]">
                    !{review.mr_iid}
                  </UiBadge>
                  {review.engine_used ? (
                    // Issue #76：紧跟标题展示引擎名，便于运维快速识别本条评审的引擎。
                    <span className="inline-flex items-center rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 text-[10px] font-mono text-zinc-600">
                      {review.engine_used}
                    </span>
                  ) : null}
                </div>
                <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
                  {review.project_path} · {relativeTime(review.created_at)}
                </div>
              </div>
              {(() => {
                const badge = reviewStatusBadgeProps(review.status, review.has_blocker);
                return (
                  <UiBadge variant={badge.variant} className={badge.className} title={badge.title}>
                    {badge.label}
                  </UiBadge>
                );
              })()}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

type ProjectCardProps = {
  project: ProjectConfig;
  providerOptions: SelectOption[];
  rules: RuleConfig[];
  onSavePolicies: (projectId: string, policies: BlockPolicyPayload[]) => Promise<void>;
  onSaveProject: (projectId: string, payload: ProjectUpdatePayload) => Promise<void>;
  onDeleteProject: (id: string) => Promise<void>;
};

type ProjectEditForm = {
  name: string;
  gitlab_project_id: string;
  gitlab_access_token: string;
  webhook_secret: string;
  provider_id: string;
  // Issue #73：编辑项目时也允许勾选启用规则。
  rules: ProjectRuleFormPayload[];
};

function ProjectCard({ project, providerOptions, rules, onSavePolicies, onSaveProject, onDeleteProject }: ProjectCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editSaving, setEditSaving] = useState(false);
  const [editForm, setEditForm] = useState<ProjectEditForm>({
    name: '',
    gitlab_project_id: '',
    gitlab_access_token: '',
    webhook_secret: '',
    provider_id: '',
    rules: [],
  });
  const letter = project.name.charAt(0).toUpperCase();

  function startEdit() {
    // 两个敏感字段留空表示不修改，避免把脱敏的 **** 误回写。
    // rules 预填当前项目已启用的规则，rule_id 存的是规则表的 UUID（后端 FK），与"新建项目"表单一致。
    setEditForm({
      name: project.name,
      gitlab_project_id: project.gitlab_project_id,
      gitlab_access_token: '',
      webhook_secret: '',
      provider_id: project.provider_id ?? '',
      rules: project.rules.map((rule) => ({ rule_id: rule.rule_id, enabled: rule.enabled })),
    });
    setEditing(true);
  }

  async function handleSaveEdit() {
    if (editSaving) {
      return;
    }
    const payload: ProjectUpdatePayload = {
      name: editForm.name,
      gitlab_project_id: editForm.gitlab_project_id,
      provider_id: editForm.provider_id || null,
      rules: editForm.rules,
    };
    if (editForm.gitlab_access_token.trim()) {
      payload.gitlab_access_token = editForm.gitlab_access_token;
    }
    if (editForm.webhook_secret.trim()) {
      payload.webhook_secret = editForm.webhook_secret;
    }
    try {
      setEditSaving(true);
      await onSaveProject(project.id, payload);
      setEditing(false);
    } finally {
      setEditSaving(false);
    }
  }

  async function handleDelete() {
    // Issue #73：项目删除不可撤销，弹二次确认。
    if (!window.confirm(`确定删除项目「${project.name}」？此操作不可撤销。`)) {
      return;
    }
    await onDeleteProject(project.id);
  }

  return (
    <div className="border-b border-zinc-100 last:border-b-0">
      <div className="flex items-center justify-between px-4 py-3 hover:bg-zinc-50 transition-colors">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 rounded-md bg-indigo-50 flex items-center justify-center shrink-0">
            <span className="text-[13px] font-semibold text-indigo-700">{letter}</span>
          </div>
          <div className="min-w-0">
            <div className="text-[13px] font-medium text-zinc-900 truncate">{project.name}</div>
            <div className="text-[11px] text-zinc-500 mt-0.5 font-mono truncate">
              GitLab {project.gitlab_project_id} · {project.default_block_severity} · {project.block_policies.length} 条策略
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <UiBadge variant={project.enabled ? 'success' : 'default'}>
            <span className={cn('w-1.5 h-1.5 rounded-full', project.enabled ? 'bg-emerald-500' : 'bg-zinc-400')} />
            {project.enabled ? '启用' : '停用'}
          </UiBadge>
          <Button variant="ghost" size="sm" type="button" onClick={startEdit}>编辑</Button>
          <Button variant="destructive" size="sm" type="button" onClick={() => void handleDelete()}>删除</Button>
          <Button variant="secondary" size="sm" type="button" onClick={() => setExpanded((prev) => !prev)}>
            {expanded ? '收起策略' : '展开策略'}
          </Button>
        </div>
      </div>
      {editing ? (
        <div className="space-y-3 border-t border-zinc-100 bg-zinc-50 px-4 pb-4 pt-3">
          <TextInput label="项目名称" value={editForm.name} onChange={(value) => setEditForm({ ...editForm, name: value })} />
          <TextInput label="GitLab Project ID" value={editForm.gitlab_project_id} onChange={(value) => setEditForm({ ...editForm, gitlab_project_id: value })} />
          <TextInput label="GitLab Access Token" type="password" placeholder="留空则不修改" value={editForm.gitlab_access_token} onChange={(value) => setEditForm({ ...editForm, gitlab_access_token: value })} />
          <TextInput label="Webhook Secret" type="password" placeholder="留空则不修改" value={editForm.webhook_secret} onChange={(value) => setEditForm({ ...editForm, webhook_secret: value })} />
          <SelectInput label="AI 供应商" value={editForm.provider_id} options={providerOptions} onChange={(value) => setEditForm({ ...editForm, provider_id: value })} />
          {/* Issue #73：启用规则多选列表，样式与"新建项目"表单对齐。 */}
          <div>
            <label className="text-[12px] font-medium text-zinc-600 mb-2 block">启用规则</label>
            <div className="rounded-md border border-zinc-200 max-h-48 overflow-y-auto">
              {rules.length === 0 ? (
                <div className="p-3 text-[12px] text-zinc-500">暂无规则，请先到"审查规则"页面创建。</div>
              ) : (
                rules.map((rule) => (
                  <label key={rule.id} className="flex items-center gap-2 px-3 py-2 border-b border-zinc-100 last:border-b-0 text-[13px] cursor-pointer hover:bg-white">
                    <input
                      type="checkbox"
                      className="size-4 rounded border-zinc-300 accent-indigo-600"
                      checked={editForm.rules.some((selected) => selected.rule_id === rule.id)}
                      onChange={(event) => setEditForm((prev) => ({ ...prev, rules: toggleRuleSelection(prev.rules, rule.id, event.target.checked) }))}
                    />
                    <span className="text-zinc-900">{rule.rule_id}</span>
                    <span className="text-zinc-500">·</span>
                    <span className="text-zinc-600 truncate">{rule.title}</span>
                  </label>
                ))
              )}
            </div>
          </div>
          <div className="flex items-center justify-end gap-2 pt-1">
            <Button variant="secondary" size="sm" type="button" disabled={editSaving} onClick={() => setEditing(false)}>取消</Button>
            <Button size="sm" type="button" disabled={editSaving} onClick={() => void handleSaveEdit()}>{editSaving ? '保存中…' : '保存'}</Button>
          </div>
          <div className="text-[11px] text-zinc-500">
            阻断策略请点"展开策略"编辑。
          </div>
        </div>
      ) : null}
      {expanded ? (
        <div className="px-4 pb-4 bg-zinc-50 border-t border-zinc-100">
          <BlockPolicyTable projectId={project.id} policies={project.block_policies} onSave={onSavePolicies} />
        </div>
      ) : null}
    </div>
  );
}

type EditableBlockPolicy = {
  key: string;
  branch_pattern: string;
  block_severity: BlockPolicySeverity;
  block_on_engine_error: boolean;
  require_all_resolved: boolean;
};

let blockPolicyKeySeed = 0;

function toEditablePolicy(policy: BlockPolicy): EditableBlockPolicy {
  return {
    key: policy.id,
    branch_pattern: policy.branch_pattern,
    block_severity: policy.block_severity,
    block_on_engine_error: policy.block_on_engine_error,
    require_all_resolved: policy.require_all_resolved,
  };
}

type BlockPolicyTableProps = {
  projectId: string;
  policies: BlockPolicy[];
  onSave: (projectId: string, policies: BlockPolicyPayload[]) => Promise<void>;
};

function BlockPolicyTable({ projectId, policies, onSave }: BlockPolicyTableProps) {
  const [items, setItems] = useState<EditableBlockPolicy[]>(() => policies.map(toEditablePolicy));
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setItems(policies.map(toEditablePolicy));
  }, [policies]);

  function updateItem(index: number, patch: Partial<EditableBlockPolicy>) {
    setItems((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  }

  function addPolicy() {
    blockPolicyKeySeed += 1;
    setItems((prev) => [
      ...prev,
      {
        key: `new-policy-${blockPolicyKeySeed}`,
        branch_pattern: '',
        block_severity: 'WARNING',
        block_on_engine_error: false,
        require_all_resolved: false,
      },
    ]);
  }

  function removePolicy(index: number) {
    setItems((prev) => prev.filter((_, i) => i !== index));
  }

  function handleDragStart(index: number) {
    setDragIndex(index);
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
  }

  function handleDrop(index: number) {
    if (dragIndex === null || dragIndex === index) {
      setDragIndex(null);
      return;
    }
    setItems((prev) => {
      const next = [...prev];
      const [moved] = next.splice(dragIndex, 1);
      next.splice(index, 0, moved);
      return next;
    });
    setDragIndex(null);
  }

  async function handleSave() {
    if (saving) {
      return;
    }
    const payload: BlockPolicyPayload[] = items.map((item, index) => ({
      branch_pattern: item.branch_pattern,
      block_severity: item.block_severity,
      block_on_engine_error: item.block_on_engine_error,
      require_all_resolved: item.require_all_resolved,
      priority: index + 1,
    }));
    try {
      setSaving(true);
      await onSave(projectId, payload);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="policy-editor">
      <div className="policy-head muted">
        <span />
        <span>序号</span>
        <span>分支匹配</span>
        <span>阻断级别</span>
        <span>引擎错误阻断</span>
        <span>操作</span>
      </div>
      {items.length === 0 ? <div className="empty">暂无阻断策略，点击下方按钮添加。</div> : null}
      {items.map((item, index) => (
        <div
          key={item.key}
          className={`policy-row${dragIndex === index ? ' dragging' : ''}`}
          onDragOver={handleDragOver}
          onDrop={() => handleDrop(index)}
        >
          <span
            className="drag-handle"
            draggable
            aria-label="拖动排序"
            onDragStart={() => handleDragStart(index)}
            onDragEnd={() => setDragIndex(null)}
          >
            ⠿
          </span>
          <span className="priority">{index + 1}</span>
          <input
            className="branch-pattern"
            value={item.branch_pattern}
            placeholder="如 master 或 release/*"
            onChange={(event) => updateItem(index, { branch_pattern: event.target.value })}
          />
          <select
            className="severity-select"
            value={item.block_severity}
            onChange={(event) => updateItem(index, { block_severity: event.target.value as BlockPolicySeverity })}
          >
            {BLOCK_POLICY_SEVERITY_OPTIONS.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
          <input
            type="checkbox"
            checked={item.block_on_engine_error}
            aria-label="引擎错误时阻断"
            onChange={(event) => updateItem(index, { block_on_engine_error: event.target.checked })}
          />
          <Button variant="destructive" type="button" onClick={() => removePolicy(index)}>删除</Button>
        </div>
      ))}
      <div className="policy-actions">
        <Button variant="outline" type="button" onClick={addPolicy}>添加策略</Button>
        <Button type="button" disabled={saving} onClick={() => void handleSave()}>
          {saving ? '保存中…' : '保存策略'}
        </Button>
      </div>
    </div>
  );
}

type ReviewRecordRowProps = {
  review: ReviewRecord;
  onError: (caught: unknown) => void;
};

function ReviewRecordRow({ review, onError }: ReviewRecordRowProps) {
  const [expanded, setExpanded] = useState(false);
  const [findings, setFindings] = useState<FindingRecord[] | null>(null);
  const [loadingFindings, setLoadingFindings] = useState(false);

  async function toggleExpand() {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (findings !== null) {
      return;
    }
    setLoadingFindings(true);
    try {
      setFindings(await fetchReviewFindings(review.id));
    } catch (caught) {
      onError(caught);
      setFindings(null);
    } finally {
      setLoadingFindings(false);
    }
  }

  return (
    <div className="border-b border-zinc-100 last:border-b-0">
      <div className="flex items-center justify-between px-4 py-3 hover:bg-zinc-50 transition-colors">
        <div className="flex items-center gap-3 min-w-0">
          <span
            className={cn(
              'w-1.5 h-1.5 rounded-full shrink-0',
              review.status === 'engine_error'
                ? review.has_blocker
                  ? 'bg-rose-500'
                  : 'bg-amber-500'
                : review.has_blocker
                  ? 'bg-rose-500'
                  : 'bg-emerald-500',
            )}
          />
          <div className="min-w-0">
            <div className="text-[13px] font-medium text-zinc-900 truncate">
              MR !{review.mr_iid} <span className="text-zinc-500">{review.source_branch} → {review.target_branch}</span>
            </div>
            <div className="text-[11px] text-zinc-500 mt-0.5 font-mono truncate">
              {review.status} · {review.finding_count} 个问题 · {review.commit_sha.slice(0, 7)} · {review.project_name || '-'}
              {review.created_at ? ` · ${relativeTime(review.created_at)}` : ''}
            </div>
            {(review.rules_used ?? []).length > 0 || review.engine_used ? (
              <div className="mt-1 flex flex-wrap gap-1">
                {(review.rules_used ?? []).map((ruleId) => (
                  <span key={ruleId} className="inline-flex items-center rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 text-[10px] font-mono text-zinc-600">
                    {ruleId}
                  </span>
                ))}
                {review.engine_used ? (
                  // Issue #76：引擎徽章紧跟规则标签末尾，用不同色区分。
                  <span className="inline-flex items-center rounded border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[10px] font-mono text-indigo-700">
                    {review.engine_used}
                  </span>
                ) : null}
              </div>
            ) : (
              <div className="mt-1 text-[10px] text-zinc-400">-</div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {(() => {
            const badge = reviewStatusBadgeProps(review.status, review.has_blocker);
            return (
              <UiBadge variant={badge.variant} className={badge.className} title={badge.title}>
                {badge.label}
              </UiBadge>
            );
          })()}
          <Button variant="ghost" size="sm" type="button" onClick={() => void toggleExpand()}>
            {expanded ? '收起问题' : '查看问题'}
          </Button>
        </div>
      </div>
      {expanded ? (
        <div className="bg-zinc-50 border-t border-zinc-100 px-4 pb-4">
          {loadingFindings ? <div className="py-3 text-[13px] text-zinc-500">加载中…</div> : null}
          {!loadingFindings && findings === null ? <div className="py-3 text-[13px] text-zinc-500">加载失败，请收起后重新展开。</div> : null}
          {!loadingFindings && findings !== null && findings.length === 0 ? <div className="py-3 text-[13px] text-zinc-500 text-center">暂无问题</div> : null}
          {(findings ?? []).map((finding) => (
            <div key={finding.id} className="flex items-start justify-between gap-3 py-3 border-b border-zinc-100 last:border-b-0">
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium text-zinc-900">{finding.title}</div>
                <div className="text-[11px] text-zinc-500 mt-0.5 font-mono truncate">{finding.file_path}:{finding.line_number ?? '-'} · {finding.rule_id} · {finding.fp_status}</div>
              </div>
              <UiBadge {...severityBadgeProps(finding.severity)}>{finding.severity}</UiBadge>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function toggleRuleSelection(
  rules: ProjectRuleFormPayload[],
  ruleId: string,
  enabled: boolean,
): ProjectRuleFormPayload[] {
  const exists = rules.some((rule) => rule.rule_id === ruleId);
  if (enabled && !exists) {
    return [...rules, { rule_id: ruleId, enabled: true }];
  }
  if (!enabled && exists) {
    return rules.filter((rule) => rule.rule_id !== ruleId);
  }
  return rules;
}

function truncate(text: string, limit = 80): string {
  const trimmed = text.trim();
  return trimmed.length > limit ? `${trimmed.slice(0, limit)}…` : trimmed;
}

/**
 * 严重级别 → UiBadge props。BLOCKER=rose(destructive)、WARNING=amber、INFO=neutral(default)。
 * `warning` 变体不存在（见 ui/badge.tsx），故 WARNING 用 className 覆盖为 amber，
 * 与 DESIGN.md 的 warning token (#F59E0B = amber-500) 对齐。
 */
function severityBadgeProps(severity: string): { variant: 'destructive' | 'default'; className?: string } {
  if (severity === 'BLOCKER') return { variant: 'destructive' };
  if (severity === 'WARNING') return { variant: 'default', className: 'border-amber-100 bg-amber-50 text-amber-700' };
  return { variant: 'default' };
}

/**
 * review.status → UiBadge props。
 *
 * - ``engine_error``：**红/橙徽章 "审查失败"**，覆盖 has_blocker 分支——AI 引擎
 *   都挂了就不能装成 "通过"。has_blocker=true 时用 destructive（rose），has_blocker=
 *   false（有些 policy 允许 fail 时放行 merge）用 amber 表明"引擎异常但未强阻"。
 * - ``done``：走原来的 has_blocker → 通过/阻断 语义。
 * - 其它未知状态：显示"未知"灰色徽章，帮助前端及时发现后端加了新状态却没更新 UI。
 */
function reviewStatusBadgeProps(
  status: string,
  hasBlocker: boolean,
): { variant: 'destructive' | 'default' | 'success'; className?: string; label: string; title?: string } {
  if (status === 'engine_error') {
    if (hasBlocker) {
      return {
        variant: 'destructive',
        label: '审查失败',
        title: 'AI 引擎调用失败，且策略阻止合并，请人工审查。',
      };
    }
    return {
      variant: 'default',
      className: 'border-amber-200 bg-amber-50 text-amber-700',
      label: '引擎异常',
      title: 'AI 引擎调用失败，策略未阻止合并，但请人工审查。',
    };
  }
  if (status === 'done') {
    return hasBlocker
      ? { variant: 'destructive', label: '阻断' }
      : { variant: 'success', label: '通过' };
  }
  return { variant: 'default', label: '未知', title: `未知状态：${status}` };
}

export function relativeTime(iso?: string): string {
  if (!iso) {
    return '';
  }
  // 防守：后端理论上已保证输出带 tz（AwareDatetime），这里再补一层兜底——
  // 如果字符串既没有 Z 也没有 +HH:MM / -HH:MM，就当作 UTC 追加 Z，
  // 避免浏览器按本地时区解析导致时差错位（见 backend/app/schemas/_datetime.py）。
  const hasTz = /[Zz]$/.test(iso) || /[+-]\d{2}:?\d{2}$/.test(iso);
  const normalized = hasTz ? iso : `${iso}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) {
    return '';
  }
  const diffSeconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (diffSeconds < 60) {
    return '刚刚';
  }
  const minutes = Math.floor(diffSeconds / 60);
  if (minutes < 60) {
    return `${minutes} 分钟前`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} 小时前`;
  }
  const days = Math.floor(hours / 24);
  if (days < 30) {
    return `${days} 天前`;
  }
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function parsePositiveInteger(value: string, label: string): number {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${label} 必须是正整数。`);
  }
  return parsed;
}

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return '未知错误';
}

export default App;
