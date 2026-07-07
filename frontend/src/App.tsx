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
  RecentReview,
  ReviewRecord,
  RuleConfig,
  RuleFormPayload,
  confirmFalsePositive,
  createProject,
  createProvider,
  createReview,
  createRule,
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
import { AppShell } from './components/layout/AppShell';
import { Badge as UiBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
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

  const blockerReviews = useMemo(
    () => reviews.filter((review) => review.has_blocker).length,
    [reviews],
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
      if (!ruleForm.rule_id.trim() || !ruleForm.title.trim() || !ruleForm.prompt_snippet.trim()) {
        throw new Error('规则 ID、标题和提示片段不能为空。');
      }
      await createRule(ruleForm);
      setRuleForm(initialRuleForm);
      setRulesPage(await fetchRules());
      setMessage('审查规则已创建。');
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
      <PanelGrid aria-busy={loading}>
        <div className="col-span-12 lg:col-span-4">
          <Card>
            <CardHeader><CardTitle>服务状态</CardTitle></CardHeader>
            <CardContent>
              <StatusRow label="API" value={health?.status === 'ok' ? '服务正常' : '服务异常'} ok={health?.status === 'ok'} />
              <StatusRow label="数据库" value={health?.db === 'ok' ? '数据库正常' : '数据库异常'} ok={health?.db === 'ok'} />
              <StatusRow label="Redis" value={health?.redis === 'ok' ? 'Redis 正常' : 'Redis 异常'} ok={health?.redis === 'ok'} />
              <p className="text-sm text-muted-foreground pt-3">版本：{health?.version ?? '加载中'}</p>
            </CardContent>
          </Card>
        </div>

        <div className="col-span-12 lg:col-span-4">
          <Card>
            <CardHeader><CardTitle>引擎状态</CardTitle></CardHeader>
            <CardContent>
              {engines.length === 0 ? <div className="text-sm text-muted-foreground py-4 text-center">暂无已注册引擎</div> : null}
              {engines.map((engine) => (
                <div className="flex items-start justify-between gap-3 border-b border-border py-3 last:border-b-0" key={engine.name}>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-sm">{engine.name}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {engine.requires_repo_clone ? '需要克隆仓库' : '无需克隆仓库'} ·
                      {engine.supports_feedback ? ' 支持反馈' : ' 暂不支持反馈'}
                    </div>
                  </div>
                  <Badge ok={engine.healthy}>{engine.health_status}</Badge>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="col-span-12 lg:col-span-4">
          <Card>
            <CardHeader><CardTitle>审查概览</CardTitle></CardHeader>
            <CardContent>
              <StatusRow label="最近审查" value={`${reviews.length} 次`} ok />
              <StatusRow label="存在阻断" value={`${blockerReviews} 次`} ok={blockerReviews === 0} />
              <StatusRow label="可手动触发" value="已启用" ok />
            </CardContent>
          </Card>
        </div>

        <div className="col-span-12">
          <Card>
            <CardHeader><CardTitle>手动触发 MR 审查</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <form className="grid gap-3 md:grid-cols-2" onSubmit={handleSubmit}>
                <TextInput label="内部调用 Token" value={form.internalToken} type="password" onChange={(value) => setForm({ ...form, internalToken: value })} />
                <TextInput label="GitLab 项目 ID" value={form.projectId} onChange={(value) => setForm({ ...form, projectId: value })} />
                <TextInput label="MR IID" value={form.mrIid} onChange={(value) => setForm({ ...form, mrIid: value })} />
                <TextInput label="目标分支" value={form.targetBranch} onChange={(value) => setForm({ ...form, targetBranch: value })} />
                <TextInput label="源分支" value={form.sourceBranch} onChange={(value) => setForm({ ...form, sourceBranch: value })} />
                <TextInput label="Commit SHA" value={form.commitSha} onChange={(value) => setForm({ ...form, commitSha: value })} />
                <TextInput label="项目路径（可选）" value={form.projectPath} onChange={(value) => setForm({ ...form, projectPath: value })} />
                <TextInput label="MR 标题（可选）" value={form.title} onChange={(value) => setForm({ ...form, title: value })} />
                <TextInput label="MR URL（可选）" value={form.webUrl} onChange={(value) => setForm({ ...form, webUrl: value })} />
                <div className="flex items-center gap-2 flex-wrap md:col-span-2">
                  <Button disabled={submitting} type="submit">{submitting ? '审查中…' : '触发审查'}</Button>
                  <Button disabled={submitting} type="button" variant="outline" onClick={handleRefreshReviews}>刷新最近审查</Button>
                  <span className="text-sm text-muted-foreground">Token 只在本次请求中使用，不会保存到前端状态之外。</span>
                </div>
              </form>
              {submitResult ? (
                <div className={submitResult.has_blocker ? 'rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive' : 'rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm text-primary'}>
                  {submitResult.has_blocker ? '审查完成，发现阻断问题。' : '审查完成，未发现阻断问题。'}
                  {submitResult.review_url ? <> <a href={submitResult.review_url} className="text-primary hover:underline">查看结果</a></> : null}
                </div>
              ) : null}
            </CardContent>
          </Card>
        </div>

        <RecentReviewsCard reviews={reviews} />
      </PanelGrid>
    );
  }

  function renderProviders() {
    return (
      <PanelGrid>
        <div className="col-span-12 lg:col-span-5">
          <Card>
            <CardHeader><CardTitle>新增模型供应商</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <form className="grid gap-3" onSubmit={handleCreateProvider}>
                <TextInput label="供应商名称" value={providerForm.name} onChange={(value) => setProviderForm({ ...providerForm, name: value })} />
                <SelectInput label="协议" value={providerForm.protocol} options={['openai_compatible', 'anthropic', 'custom']} onChange={(value) => setProviderForm({ ...providerForm, protocol: value as ProviderFormPayload['protocol'] })} />
                <TextInput label="Base URL" value={providerForm.base_url} onChange={(value) => setProviderForm({ ...providerForm, base_url: value })} />
                <TextInput label="API Key" type="password" value={providerForm.api_key} onChange={(value) => setProviderForm({ ...providerForm, api_key: value })} />
                <TextInput label="模型名" value={providerForm.model} onChange={(value) => setProviderForm({ ...providerForm, model: value })} />
                <TextInput label="最大 Token" value={String(providerForm.max_tokens)} onChange={(value) => setProviderForm({ ...providerForm, max_tokens: Number(value) || 0 })} />
                <div className="flex items-center gap-2 flex-wrap"><Button type="submit">保存供应商</Button></div>
              </form>
            </CardContent>
          </Card>
        </div>
        <ListCard title="模型供应商列表" empty="暂无模型供应商">
          {(providersPage?.items ?? []).map((provider) => (
            <DataRow key={provider.id} title={provider.name} meta={`${provider.protocol} · ${provider.model} · ${provider.base_url}`} status={provider.enabled ? '启用' : '停用'} ok={provider.enabled} />
          ))}
        </ListCard>
      </PanelGrid>
    );
  }

  function renderRules() {
    return (
      <PanelGrid>
        <div className="col-span-12 lg:col-span-5">
          <Card>
            <CardHeader><CardTitle>新增审查规则</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <form className="grid gap-3" onSubmit={handleCreateRule}>
                <TextInput label="规则 ID" value={ruleForm.rule_id} onChange={(value) => setRuleForm({ ...ruleForm, rule_id: value })} />
                <TextInput label="规则标题" value={ruleForm.title} onChange={(value) => setRuleForm({ ...ruleForm, title: value })} />
                <TextAreaInput label="提示片段" value={ruleForm.prompt_snippet} onChange={(value) => setRuleForm({ ...ruleForm, prompt_snippet: value })} />
                <SelectInput label="默认严重级别" value={ruleForm.severity_default} options={BLOCK_SEVERITY_OPTIONS} onChange={(value) => setRuleForm({ ...ruleForm, severity_default: value as RuleFormPayload['severity_default'] })} />
                <CheckboxInput label="启用规则" checked={ruleForm.enabled} onChange={(value) => setRuleForm({ ...ruleForm, enabled: value })} />
                <div className="flex items-center gap-2 flex-wrap"><Button type="submit">保存规则</Button></div>
              </form>
            </CardContent>
          </Card>
        </div>
        <ListCard title="审查规则" empty="暂无审查规则">
          {(rulesPage?.items ?? []).map((rule) => (
            <DataRow key={rule.id} title={`${rule.rule_id} · ${rule.title}`} meta={`${rule.severity_default} · ${truncate(rule.prompt_snippet)}`} status={rule.enabled ? '启用' : '停用'} ok={rule.enabled} />
          ))}
        </ListCard>
      </PanelGrid>
    );
  }

  function renderProjects() {
    const engineOptions: SelectOption[] = [
      { value: '', label: '不指定' },
      ...(engineConfigsPage?.items ?? []).map((engine) => ({ value: engine.id, label: engine.name })),
    ];
    return (
      <PanelGrid>
        <div className="col-span-12 lg:col-span-5">
          <Card>
            <CardHeader><CardTitle>新增 GitLab 项目</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <form className="grid gap-3" onSubmit={handleCreateProject}>
                <TextInput label="项目名称" value={projectForm.name} onChange={(value) => setProjectForm({ ...projectForm, name: value })} />
                <TextInput label="GitLab Project ID" value={projectForm.gitlab_project_id} onChange={(value) => setProjectForm({ ...projectForm, gitlab_project_id: value })} />
                <TextInput label="GitLab Access Token" type="password" value={projectForm.gitlab_access_token} onChange={(value) => setProjectForm({ ...projectForm, gitlab_access_token: value })} />
                <TextInput label="Webhook Secret" type="password" value={projectForm.webhook_secret} onChange={(value) => setProjectForm({ ...projectForm, webhook_secret: value })} />
                <SelectInput label="默认审查引擎" value={projectForm.engine_id} options={engineOptions} onChange={(value) => setProjectForm({ ...projectForm, engine_id: value })} />
                <TextInput label="超时秒数" value={String(projectForm.timeout_seconds)} onChange={(value) => setProjectForm({ ...projectForm, timeout_seconds: Number(value) || 0 })} />
                <TextInput label="最大文件数" value={String(projectForm.max_files)} onChange={(value) => setProjectForm({ ...projectForm, max_files: Number(value) || 0 })} />
                <SelectInput label="默认阻断级别" value={projectForm.default_block_severity} options={BLOCK_SEVERITY_OPTIONS} onChange={(value) => setProjectForm({ ...projectForm, default_block_severity: value as ProjectFormPayload['default_block_severity'] })} />
                <fieldset className="rounded-md border border-input p-3 space-y-2">
                  <legend className="text-sm font-medium px-1">启用规则</legend>
                  {(rulesPage?.items ?? []).length === 0 ? <div className="text-sm text-muted-foreground">暂无规则，请先到“审查规则”页面创建。</div> : null}
                  {(rulesPage?.items ?? []).map((rule) => (
                    <label key={rule.id} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="size-4 rounded border-input accent-primary"
                        checked={projectForm.rules.some((selected) => selected.rule_id === rule.id)}
                        onChange={(event) => setProjectForm((prev) => ({ ...prev, rules: toggleRuleSelection(prev.rules, rule.id, event.target.checked) }))}
                      />
                      <span>{rule.rule_id} · {rule.title}</span>
                    </label>
                  ))}
                </fieldset>
                <div className="flex items-center gap-2 flex-wrap"><Button type="submit">保存项目</Button></div>
              </form>
            </CardContent>
          </Card>
        </div>
        <ListCard title="GitLab 项目列表" empty="暂无 GitLab 项目">
          {(projectsPage?.items ?? []).map((project) => (
            <ProjectCard key={project.id} project={project} onSavePolicies={handleSaveBlockPolicies} />
          ))}
        </ListCard>
      </PanelGrid>
    );
  }

  function renderReviewRecords() {
    return (
      <PanelGrid>
        <ListCard title="审查记录" empty="暂无审查记录">
          {(reviewRecordsPage?.items ?? []).map((review) => (
            <ReviewRecordRow key={review.id} review={review} onError={handleCaughtError} />
          ))}
        </ListCard>
        <RecentReviewsCard reviews={reviews} />
      </PanelGrid>
    );
  }

  function renderFindings() {
    return (
      <PanelGrid>
        <div className="col-span-12">
          <Card>
            <CardHeader><CardTitle>问题与误报</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-end gap-3 mb-4">
                <TextInput label="操作人" value={operator} onChange={setOperator} />
                <TextInput label="处理说明" value={reviewNote} onChange={setReviewNote} />
              </div>
              {(findingsPage?.items ?? []).length === 0 ? <div className="text-sm text-muted-foreground py-4 text-center">暂无问题记录</div> : null}
              {(findingsPage?.items ?? []).map((finding) => (
                <article className="flex items-start justify-between gap-3 border-b border-border py-3 last:border-b-0" key={finding.id}>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-sm">{finding.title}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">{finding.file_path}:{finding.line_number ?? '-'} · {finding.rule_id} · {finding.fp_status}</div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Badge ok={finding.severity !== 'BLOCKER'}>{finding.severity}</Badge>
                    <Button type="button" onClick={() => void handleMarkFalsePositive(finding)}>标记误报</Button>
                  </div>
                </article>
              ))}
            </CardContent>
          </Card>
        </div>
        <NegativeExamplesCard examples={negativeExamplesPage?.items ?? []} />
      </PanelGrid>
    );
  }

  function renderFalsePositives() {
    return (
      <PanelGrid>
        <div className="col-span-12">
          <Card>
            <CardHeader><CardTitle>误报队列</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-end gap-3 mb-4">
                <TextInput label="审核人" value={operator} onChange={setOperator} />
                <TextInput label="审核备注" value={reviewNote} onChange={setReviewNote} />
              </div>
              {(pendingFpPage?.items ?? []).length === 0 ? <div className="text-sm text-muted-foreground py-4 text-center">暂无待确认误报</div> : null}
              {(pendingFpPage?.items ?? []).map((finding) => (
                <article className="flex items-start justify-between gap-3 border-b border-border py-3 last:border-b-0" key={finding.id}>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-sm">{finding.title}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">{finding.file_path}:{finding.line_number ?? '-'} · {finding.fp_marked_by ?? '未知提交人'} · {finding.fp_marked_reason ?? '无原因'}</div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Button type="button" onClick={() => void handleReviewFalsePositive(finding, 'confirm')}>确认误报</Button>
                    <Button variant="outline" type="button" onClick={() => void handleReviewFalsePositive(finding, 'reject')}>驳回</Button>
                  </div>
                </article>
              ))}
            </CardContent>
          </Card>
        </div>
        <NegativeExamplesCard examples={negativeExamplesPage?.items ?? []} />
      </PanelGrid>
    );
  }

  function renderEngineConfigs() {
    return (
      <PanelGrid>
        <ListCard title="引擎配置" empty="暂无引擎配置">
          {(engineConfigsPage?.items ?? []).map((engine) => (
            <DataRow key={engine.id} title={engine.name} meta={engine.description ?? '暂无描述'} status={engine.enabled ? '启用' : '停用'} ok={engine.enabled} />
          ))}
        </ListCard>
      </PanelGrid>
    );
  }
}

function PanelGrid({ children, ...props }: React.HTMLAttributes<HTMLElement>) {
  return (
    <section className="grid grid-cols-12 gap-4" {...props}>
      {children}
    </section>
  );
}

type TextInputProps = {
  label: string;
  value: string;
  type?: string;
  onChange: (value: string) => void;
};

function TextInput({ label, value, type = 'text', onChange }: TextInputProps) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type={type} value={value} onChange={(event) => onChange(event.target.value)} />
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

type ListCardProps = {
  title: string;
  empty: string;
  children: React.ReactNode;
};

function ListCard({ title, empty, children }: ListCardProps) {
  const hasChildren = Array.isArray(children) ? children.length > 0 : Boolean(children);
  return (
    <div className="col-span-12 lg:col-span-7">
      <Card>
        <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {hasChildren ? children : <div className="text-sm text-muted-foreground">{empty}</div>}
        </CardContent>
      </Card>
    </div>
  );
}

type DataRowProps = {
  title: string;
  meta: string;
  status: string;
  ok: boolean;
};

function DataRow({ title, meta, status, ok }: DataRowProps) {
  return (
    <article className="flex items-start justify-between gap-3 border-b border-border py-3 last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="font-medium text-sm">{title}</div>
        <div className="text-xs text-muted-foreground mt-0.5 truncate">{meta}</div>
      </div>
      <Badge ok={ok}>{status}</Badge>
    </article>
  );
}

function RecentReviewsCard({ reviews }: { reviews: RecentReview[] }) {
  return (
    <div className="col-span-12">
      <Card>
        <CardHeader><CardTitle>最近审查记录</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {reviews.length === 0 ? <div className="text-sm text-muted-foreground">暂无审查记录</div> : null}
          {reviews.map((review) => (
            <article className="flex items-start justify-between gap-3 border-b border-border py-3 last:border-b-0" key={`${review.review_id ?? review.project_id}-${review.mr_iid}`}>
              <div className="min-w-0 flex-1">
                <div className="font-medium text-sm">{review.title}</div>
                <div className="text-xs text-muted-foreground mt-0.5">
                  {review.project_path} !{review.mr_iid} · {review.status} · {review.finding_count} 个问题
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Badge ok={!review.has_blocker}>{review.has_blocker ? '阻断' : '通过'}</Badge>
                {review.review_url ? <a href={review.review_url} className="text-xs text-primary hover:underline">结果</a> : null}
              </div>
            </article>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function NegativeExamplesCard({ examples }: { examples: NegativeExample[] }) {
  return (
    <div className="col-span-12">
      <Card>
        <CardHeader><CardTitle>负例库</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {examples.length === 0 ? <div className="text-sm text-muted-foreground">暂无负例</div> : null}
          {examples.map((example) => (
            <article className="flex items-start justify-between gap-3 border-b border-border py-3 last:border-b-0" key={example.id}>
              <div className="min-w-0 flex-1">
                <div className="font-medium text-sm">{example.rule_id}</div>
                <div className="text-xs text-muted-foreground mt-0.5">{example.code_snippet}</div>
              </div>
              <Badge ok>{example.approved_by ?? '已确认'}</Badge>
            </article>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

type ProjectCardProps = {
  project: ProjectConfig;
  onSavePolicies: (projectId: string, policies: BlockPolicyPayload[]) => Promise<void>;
};

function ProjectCard({ project, onSavePolicies }: ProjectCardProps) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-lg border border-border p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium text-sm">{project.name}</div>
          <div className="text-xs text-muted-foreground mt-0.5">
            GitLab ID {project.gitlab_project_id} · {project.default_block_severity} · {project.block_policies.length} 条阻断策略
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Badge ok={project.enabled}>{project.enabled ? '启用' : '停用'}</Badge>
          <Button variant="ghost" size="sm" type="button" onClick={() => setExpanded((prev) => !prev)}>
            {expanded ? '收起策略' : '展开策略'}
          </Button>
        </div>
      </div>
      {expanded ? (
        <BlockPolicyTable projectId={project.id} policies={project.block_policies} onSave={onSavePolicies} />
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
    <article>
      <div className="flex items-start justify-between gap-3 border-b border-border py-3 last:border-b-0">
        <div className="min-w-0 flex-1">
          <div className="font-medium text-sm">MR !{review.mr_iid} · {review.source_branch} → {review.target_branch}</div>
          <div className="text-xs text-muted-foreground mt-0.5">{review.status} · {review.finding_count} 个问题 · {review.commit_sha}</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Badge ok={!review.has_blocker}>{review.has_blocker ? '阻断' : '通过'}</Badge>
          <Button variant="ghost" size="sm" type="button" onClick={() => void toggleExpand()}>
            {expanded ? '收起问题' : '查看问题'}
          </Button>
        </div>
      </div>
      {expanded ? (
        <div className="mt-2 space-y-2">
          {loadingFindings ? <div className="text-sm text-muted-foreground">加载中…</div> : null}
          {!loadingFindings && findings === null ? <div className="text-sm text-muted-foreground">加载失败，请收起后重新展开。</div> : null}
          {!loadingFindings && findings !== null && findings.length === 0 ? <div className="text-sm text-muted-foreground py-4 text-center">暂无问题</div> : null}
          {(findings ?? []).map((finding) => (
            <article className="flex items-start justify-between gap-3 border-b border-border py-3 last:border-b-0" key={finding.id}>
              <div className="min-w-0 flex-1">
                <div className="font-medium text-sm">{finding.title}</div>
                <div className="text-xs text-muted-foreground mt-0.5">{finding.file_path}:{finding.line_number ?? '-'} · {finding.rule_id} · {finding.fp_status}</div>
              </div>
              <Badge ok={finding.severity !== 'BLOCKER'}>{finding.severity}</Badge>
            </article>
          ))}
        </div>
      ) : null}
    </article>
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
