import { FormEvent, DragEvent, useEffect, useId, useMemo, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';

import {
  BlockPolicy,
  BlockPolicyPayload,
  BlockPolicySeverity,
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
  RuleFormPayload,
  confirmFalsePositive,
  createProject,
  createProvider,
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
  fetchStatsCategories,
  fetchStatsOverview,
  fetchStatsProjects,
  fetchStatsRules,
  fetchStatsTimeseries,
  fetchGlobalPrompt,
  updateGlobalPrompt,
  clearStoredAdminAccessToken,
  getStoredAdminAccessToken,
  getStoredAdminUsername,
  isAuthRequiredError,
  loginAdmin,
  markFalsePositive,
  resolveFinding,
  rejectFalsePositive,
  type CategoryStat,
  type ProjectStat,
  type RuleStat,
  type StatsOverview,
  type TimeseriesPoint,
  RuleCreatePayload,
} from './api';
import { AlertOctagon, AlertTriangle, Boxes, Cpu, FileX, Filter, FolderGit2, Inbox, ListChecks, ScrollText, Settings, Sparkles, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { AppShell } from './components/layout/AppShell';
import { Badge as UiBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { RuleSelector } from './components/RuleSelector';
import { EmptyState } from './components/EmptyState';
import { PageHeader } from './components/PageHeader';
import { MarkFalsePositiveDialog } from './components/dialogs/MarkFalsePositiveDialog';
import { ResolveDialog } from './components/dialogs/ResolveDialog';
import { ReviewFalsePositiveDialog } from './components/dialogs/ReviewFalsePositiveDialog';
import { ProviderDialog } from './components/dialogs/ProviderDialog';
import { RuleDialog } from './components/dialogs/RuleDialog';
import { ProjectDialog } from './components/dialogs/ProjectDialog';
import { NegativePromptDialog } from './components/dialogs/NegativePromptDialog';
import { UserMappingsPage } from './components/UserMappingsPage';
import { LoginPage } from './pages/LoginPage';
import { categoryDisplay, severityDisplay, SEVERITY_ORDER, Severity, isKnownSeverity } from './lib/findingTaxonomy';

// PR-B：新增独立页「负样本库」，放在「误报队列」之后。
type PageKey =
  | 'dashboard'
  | 'providers'
  | 'global-prompt'
  | 'rules'
  | 'projects'
  | 'user-mappings'
  | 'reviews'
  | 'findings'
  | 'falsePositives'
  | 'negativeExamples'
  | 'engines';

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
  gitlab_base_url: '',
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
  tags: [],
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
  { key: 'global-prompt', label: '全局提示词' },
  { key: 'rules', label: '审查规则' },
  { key: 'projects', label: 'GitLab 项目' },
  { key: 'user-mappings', label: '用户映射' },
  { key: 'reviews', label: '审查记录' },
  { key: 'findings', label: '问题与误报' },
  { key: 'falsePositives', label: '误报队列' },
  { key: 'negativeExamples', label: '负样本库' },
  { key: 'engines', label: '引擎配置' },
];
type StatsBundle = {
  overview: StatsOverview | null;
  rules: RuleStat[];
  projects: ProjectStat[];
  categories: CategoryStat[];
  timeseries: TimeseriesPoint[];
};

const EMPTY_STATS_BUNDLE: StatsBundle = {
  overview: null,
  rules: [],
  projects: [],
  categories: [],
  timeseries: [],
};


function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [adminToken, setAdminToken] = useState(() => getStoredAdminAccessToken());
  const [loginForm, setLoginForm] = useState<LoginFormState>(initialLoginForm);

  // 从 URL path 读取初始页面（/dashboard, /providers, ...），刷新保持不变
  const getInitialPage = (): PageKey => {
    const path = location.pathname.replace(/^\//, '') as PageKey;
    if (path && navItems.some((item) => item.key === path)) {
      return path;
    }
    return 'dashboard';
  };

  const [activePage, setActivePageState] = useState<PageKey>(getInitialPage);

  // setActivePage 同时更新 URL path
  const setActivePage = (page: PageKey) => {
    setActivePageState(page);
    navigate(`/${page}`, { replace: true });
  };

  // URL 变化（浏览器后退/前进）时同步 activePage
  useEffect(() => {
    const path = location.pathname.replace(/^\//, '') as PageKey;
    if (path && navItems.some((item) => item.key === path)) {
      setActivePageState(path);
    }
  }, [location.pathname]);
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
  const [globalPrompt, setGlobalPrompt] = useState<string>('');
  const [globalPromptSaving, setGlobalPromptSaving] = useState(false);
  const [providerForm, setProviderForm] = useState<ProviderFormPayload>(initialProviderForm);
  const [projectForm, setProjectForm] = useState<ProjectFormPayload>(initialProjectForm);
  const [ruleForm, setRuleForm] = useState<RuleFormPayload>(initialRuleForm);
  // 每条规则的编辑态：rule.id → 编辑表单数据（null/缺省表示未进入编辑）。
  // 用 Record 而非单值，避免多条规则互相踩。
  const [ruleEdits, setRuleEdits] = useState<Record<string, RuleFormPayload | null>>({});
  // PR-B：误报处理弹窗状态。旧版页面级 operator / reviewNote state 已移除——
  // 一处输入到处生效的反直觉行为会被弹窗内表单替代。
  const [markDialogFinding, setMarkDialogFinding] = useState<FindingRecord | null>(null);
  const [resolveDialogFinding, setResolveDialogFinding] = useState<FindingRecord | null>(null);
  const [reviewDialog, setReviewDialog] = useState<{
    finding: FindingRecord;
    action: 'confirm' | 'reject';
  } | null>(null);
  // 弹窗状态：null = 关闭，{ mode: 'create' } = 新增模式，{ mode: 'edit', data: ... } = 编辑模式
  const [providerDialog, setProviderDialog] = useState<
    { mode: 'create' } | { mode: 'edit'; data: ProviderConfig } | null
  >(null);
  const [ruleDialog, setRuleDialog] = useState<
    { mode: 'create' } | { mode: 'edit'; data: RuleConfig } | null
  >(null);
  const [projectDialog, setProjectDialog] = useState<
    { mode: 'create' } | { mode: 'edit'; data: ProjectConfig } | null
  >(null);
  // PR-B：「问题与误报」页筛选器（前端 filter，findingsPage.items 量级不大）。
  const [findingsFpFilter, setFindingsFpFilter] = useState<'ALL' | 'NONE' | 'PENDING' | 'CONFIRMED' | 'REJECTED'>('ALL');
  const [findingsSeverityFilter, setFindingsSeverityFilter] = useState<Set<Severity>>(new Set());
  const [findingsSearch, setFindingsSearch] = useState('');
  // PR-B：「负样本库」筛选器。
  const [negRuleFilter, setNegRuleFilter] = useState<string>('');
  const [negSearch, setNegSearch] = useState('');
  const [loading, setLoading] = useState(true);
  // 登录页专用的提交中状态（首页手动触发 MR 审查模块已移除，submitting 不再复用）。
  const [loginSubmitting, setLoginSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [statsDays, setStatsDays] = useState<number>(30);
  const [statsBundle, setStatsBundle] = useState<StatsBundle>(EMPTY_STATS_BUNDLE);

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

  async function loadPage(page: PageKey, statsDaysOverride?: number) {
    if (!adminToken) {
      return;
    }
    const effectiveDays = statsDaysOverride ?? statsDays;
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
      } else if (page === 'user-mappings') {
        // 用户映射页复用项目列表；直接访问 #/user-mappings 时也保证已加载。
        if (!projectsPage) {
          setProjectsPage(await fetchProjects());
        }
      } else if (page === 'reviews') {
        setReviewRecordsPage(await fetchReviewRecords());
      } else if (page === 'findings') {
        // PR-B：负样本迁到独立页，问题与误报页只拉 findings。
        setFindingsPage(await fetchFindings());
      } else if (page === 'falsePositives') {
        setPendingFpPage(await fetchPendingFalsePositives());
      } else if (page === 'negativeExamples') {
        // PR-B：新页——从后端拉全部批准负样本，前端做 rule_id / 关键字过滤。
        setNegativeExamplesPage(await fetchNegativeExamples());
      } else if (page === 'dashboard') {
        const [records, recentReviews, pendingFp, overview, rules, projects, categories, timeseries] = await Promise.all([
          fetchReviewRecords(),
          fetchRecentReviews(),
          fetchPendingFalsePositives(),
          fetchStatsOverview(effectiveDays),
          fetchStatsRules(effectiveDays, 10),
          fetchStatsProjects(effectiveDays, 10),
          fetchStatsCategories(effectiveDays),
          fetchStatsTimeseries(effectiveDays),
        ]);
        setReviewRecordsPage(records);
        setReviews(recentReviews.slice(0, 5));
        setPendingFpPage(pendingFp);
        setStatsBundle({ overview, rules, projects, categories, timeseries });
      } else if (page === 'engines') {
        setEngineConfigsPage(await fetchEngineConfigs());
      } else if (page === 'global-prompt') {
        setGlobalPrompt((await fetchGlobalPrompt()).content);
      }
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoginSubmitting(true);
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
      setLoginSubmitting(false);
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

  // === 弹窗用 handler（直接接收 payload，而非表单事件） ===
  async function handleCreateProviderPayload(payload: ProviderFormPayload) {
    setError(null);
    setMessage(null);
    try {
      await createProvider(payload);
      setProvidersPage(await fetchProviders());
      setMessage('模型供应商已创建。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleCreateRulePayload(payload: RuleFormPayload) {
    setError(null);
    setMessage(null);
    try {
      if (!payload.title.trim() || !payload.prompt_snippet.trim()) {
        throw new Error('规则标题和提示片段不能为空。');
      }
      // rule_id 可选：留空则不发送该字段，由后端从标题自动生成 slug。
      const createPayload: RuleCreatePayload = {
        ...payload,
        rule_id: payload.rule_id.trim() || undefined,
      };
      await createRule(createPayload);
      setRulesPage(await fetchRules());
      setMessage('审查规则已创建。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleUpdateRulePayload(ruleId: string, payload: RuleFormPayload) {
    setError(null);
    setMessage(null);
    try {
      const updated = await updateRule(ruleId, payload);
      setRulesPage((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((rule) =>
                rule.id === updated.id ? updated : rule,
              ),
            }
          : prev,
      );
      setMessage('审查规则已更新。');
    } catch (caught) {
      handleCaughtError(caught);
    }
  }

  async function handleCreateProjectPayload(payload: ProjectFormPayload) {
    setError(null);
    setMessage(null);
    try {
      await createProject(payload);
      setProjectsPage(await fetchProjects());
      setMessage('GitLab 项目已创建。');
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
        tags: rule.tags,
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

  // PR-B：改为打开弹窗，弹窗提交时再走后端 API。
  function openMarkDialog(finding: FindingRecord) {
    setError(null);
    setMessage(null);
    setMarkDialogFinding(finding);
  }

  async function submitMarkFalsePositive(
    finding: FindingRecord,
    payload: { marked_by: string; reason: string },
  ) {
    // 让弹窗自己 catch 错误做提示；这里失败时也让父页展示错误 banner。
    try {
      await markFalsePositive(finding.id, payload);
      setFindingsPage(await fetchFindings());
      setMarkDialogFinding(null);
      setMessage('问题已标记为待确认误报。');
    } catch (caught) {
      handleCaughtError(caught);
      // 抛出让弹窗内部 setSubmitting(false) 复位 + 显示行内错误。
      throw caught;
    }
  }

  function openResolveDialog(finding: FindingRecord) {
    setError(null);
    setMessage(null);
    setResolveDialogFinding(finding);
  }

  async function submitResolve(
    finding: FindingRecord,
    payload: { resolved_by: string; reason: string },
  ) {
    // 让弹窗自己 catch 错误做提示；这里失败时也让父页展示错误 banner。
    try {
      await resolveFinding(finding.id, payload);
      setFindingsPage(await fetchFindings());
      setResolveDialogFinding(null);
      setMessage('问题已标记为已解决，MR 阻断状态已更新。');
    } catch (caught) {
      handleCaughtError(caught);
      // 抛出让弹窗内部 setSubmitting(false) 复位 + 显示行内错误。
      throw caught;
    }
  }

  function openReviewDialog(finding: FindingRecord, action: 'confirm' | 'reject') {
    setError(null);
    setMessage(null);
    setReviewDialog({ finding, action });
  }

  async function submitReviewFalsePositive(
    finding: FindingRecord,
    action: 'confirm' | 'reject',
    payload: { reviewed_by: string; note: string },
  ) {
    // note 可能是空串（confirm 时非必填），发到后端时统一转 undefined 与旧行为一致。
    const apiPayload = { reviewed_by: payload.reviewed_by, note: payload.note || undefined };
    try {
      if (action === 'confirm') {
        await confirmFalsePositive(finding.id, apiPayload);
      } else {
        await rejectFalsePositive(finding.id, apiPayload);
      }
      const [pending, negativeExamples] = await Promise.all([
        fetchPendingFalsePositives(),
        fetchNegativeExamples(),
      ]);
      setPendingFpPage(pending);
      setNegativeExamplesPage(negativeExamples);
      setReviewDialog(null);
      setMessage(action === 'confirm' ? '误报已确认并沉淀为负例。' : '误报申请已驳回。');
    } catch (caught) {
      handleCaughtError(caught);
      throw caught;
    }
  }

  if (!adminToken) {
    return (
      <LoginPage
        form={loginForm}
        onChange={(patch) => setLoginForm({ ...loginForm, ...patch })}
        onSubmit={handleLogin}
        submitting={loginSubmitting}
        error={error}
        message={message}
      />
    );
  }

  // PR-B：弹窗默认值走 sessionStorage 里存的登录用户名，缺省兜底 admin。
  const defaultOperator = getStoredAdminUsername() || 'admin';

  return (
    <AppShell activePage={activePage} onNavigate={setActivePage} health={health} onLogout={handleLogout}>
      {error ? <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{error}</div> : null}
      {message ? <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm text-primary">{message}</div> : null}
      {activePage === 'dashboard' ? renderDashboard() : null}
      {activePage === 'providers' ? renderProviders() : null}
      {activePage === 'rules' ? renderRules() : null}
      {activePage === 'projects' ? renderProjects() : null}
      {activePage === 'user-mappings' ? renderUserMappings() : null}
      {activePage === 'reviews' ? renderReviewRecords() : null}
      {activePage === 'findings' ? renderFindings() : null}
      {activePage === 'falsePositives' ? renderFalsePositives() : null}
      {activePage === 'negativeExamples' ? renderNegativeExamples() : null}
      {activePage === 'engines' ? renderEngineConfigs() : null}
      {activePage === 'global-prompt' ? renderGlobalPrompt() : null}

      {/* PR-B：误报处理弹窗。挂在 AppShell 里，便于任何页面上的按钮触发。 */}
      <MarkFalsePositiveDialog
        open={markDialogFinding !== null}
        finding={markDialogFinding}
        defaultMarkedBy={defaultOperator}
        onCancel={() => setMarkDialogFinding(null)}
        onSubmit={(payload) => {
          if (markDialogFinding) {
            return submitMarkFalsePositive(markDialogFinding, payload);
          }
          return Promise.resolve();
        }}
      />
      <ResolveDialog
        open={resolveDialogFinding !== null}
        finding={resolveDialogFinding}
        defaultResolvedBy={defaultOperator}
        onCancel={() => setResolveDialogFinding(null)}
        onSubmit={(payload) => {
          if (resolveDialogFinding) {
            return submitResolve(resolveDialogFinding, payload);
          }
          return Promise.resolve();
        }}
      />
      <ReviewFalsePositiveDialog
        open={reviewDialog !== null}
        finding={reviewDialog?.finding ?? null}
        action={reviewDialog?.action ?? 'confirm'}
        defaultReviewedBy={defaultOperator}
        onCancel={() => setReviewDialog(null)}
        onSubmit={(payload) => {
          if (reviewDialog) {
            return submitReviewFalsePositive(reviewDialog.finding, reviewDialog.action, payload);
          }
          return Promise.resolve();
        }}
      />
      {/* 模型供应商弹窗 */}
      <ProviderDialog
        open={providerDialog !== null}
        initialData={providerDialog?.mode === 'edit' ? providerDialog.data : null}
        onCancel={() => setProviderDialog(null)}
        onSubmit={async (payload) => {
          if (providerDialog?.mode === 'create') {
            await handleCreateProviderPayload(payload);
          } else if (providerDialog?.mode === 'edit') {
            await handleSaveProviderEdit(providerDialog.data.id, payload);
          }
          setProviderDialog(null);
        }}
      />
      {/* 审查规则弹窗 */}
      <RuleDialog
        open={ruleDialog !== null}
        initialData={ruleDialog?.mode === 'edit' ? ruleDialog.data : null}
        onCancel={() => setRuleDialog(null)}
        onSubmit={async (payload) => {
          if (ruleDialog?.mode === 'create') {
            await handleCreateRulePayload(payload);
          } else if (ruleDialog?.mode === 'edit') {
            await handleUpdateRulePayload(ruleDialog.data.id, payload);
          }
          setRuleDialog(null);
        }}
      />
      {/* GitLab 项目弹窗 */}
      <ProjectDialog
        open={projectDialog !== null}
        initialData={projectDialog?.mode === 'edit' ? projectDialog.data : null}
        engineOptions={[
          { value: '', label: '不指定' },
          ...(engineConfigsPage?.items ?? []).map((engine) => ({ value: engine.id, label: engine.name })),
        ]}
        providerOptions={[
          { value: '', label: '（不选择，使用默认）' },
          ...(providersPage?.items ?? []).map((provider) => ({ value: provider.id, label: provider.name })),
        ]}
        rules={rulesPage?.items ?? []}
        onCancel={() => setProjectDialog(null)}
        onSubmit={async (payload) => {
          if (projectDialog?.mode === 'create') {
            await handleCreateProjectPayload(payload);
          } else if (projectDialog?.mode === 'edit') {
            await handleSaveProjectEdit(projectDialog.data.id, payload);
          }
          setProjectDialog(null);
        }}
      />
    </AppShell>
  );

  function renderDashboard() {
    return (
      <div className="space-y-5" aria-busy={loading}>
        {/* ========== 第一行：系统级 KPI ========== */}
        <section>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <KpiCard label="总审查" value={reviewRecordsPage?.total ?? '—'} icon={ScrollText} />
            <KpiCard label="阻断问题" value={blockerCount} icon={AlertOctagon} intent="danger" />
            <KpiCard label="发现问题" value={totalFindings} icon={AlertTriangle} />
            <KpiCard label="待处理误报" value={pendingFpPage?.total ?? 0} icon={Filter} hint="待处理" />
          </div>
        </section>

        {/* ========== 第二行：系统状态 + 最近审查 ========== */}
        <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <SystemStatusCard health={health} engines={engines} />
          <RecentReviewsPanel reviews={reviews} onViewAll={() => setActivePage('reviews')} />
        </section>

        {/* ========== 第三行：统计时间选择器 ========== */}
        <section>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-zinc-900">数据统计</h2>
            <div role="group" aria-label="统计时间窗口" className="inline-flex rounded-md border border-zinc-200 bg-white p-0.5 text-[12px]">
              {[
                { value: 7, label: '最近 7 天' },
                { value: 30, label: '最近 30 天' },
                { value: 90, label: '最近 90 天' },
              ].map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => {
                    const newDays = opt.value;
                    setStatsDays(newDays);
                    void loadPage('dashboard', newDays);
                  }}
                  className={cn(
                    'px-3 py-1.5 rounded-[4px] transition-colors',
                    statsDays === opt.value
                      ? 'bg-zinc-900 text-white'
                      : 'text-zinc-600 hover:bg-zinc-100',
                  )}
                  aria-pressed={statsDays === opt.value}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </section>

        {/* ========== 第四行：时间趋势 + 问题分类分布 ========== */}
        <section className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          {/* 时间趋势 */}
          <Card className="overflow-hidden">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">审查趋势</CardTitle>
              <CardDescription>按天统计审查量、问题量、BLOCKER 量</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              {statsBundle.timeseries.length === 0 ? (
                <div className="flex flex-col items-center gap-1.5 py-8 text-center"><FileX size={18} strokeWidth={1.5} className="text-zinc-300" /><span className="text-[13px] text-zinc-400">暂无时间序列数据</span></div>
              ) : (
                <div>
                  {/* 图例 */}
                  <div className="mb-2 flex items-center gap-4 text-[11px] text-zinc-500">
                    <span className="flex items-center gap-1.5"><span className="inline-block h-2 w-2 rounded-sm bg-zinc-800" />审查量</span>
                    <span className="flex items-center gap-1.5"><span className="inline-block h-1.5 w-1.5 rounded-full bg-rose-500 ring-2 ring-white" />含 BLOCKER</span>
                  </div>
                  <TrendChart data={statsBundle.timeseries} />
                </div>
              )}
            </CardContent>
          </Card>

          {/* 问题分类分布 */}
          <Card className="overflow-hidden">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">问题分布</CardTitle>
              <CardDescription>按规则分类统计问题占比</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              {statsBundle.categories.length === 0 ? (
                <div className="flex flex-col items-center gap-1.5 py-8 text-center"><FileX size={18} strokeWidth={1.5} className="text-zinc-300" /><span className="text-[13px] text-zinc-400">暂无分类数据</span></div>
              ) : (
                <div className="space-y-2">
                  {statsBundle.categories.map((cat) => {
                    const cd = categoryDisplay(cat.category);
                    const total = statsBundle.categories.reduce((sum, c) => sum + c.count, 0);
                    const pct = total > 0 ? Math.round((cat.count / total) * 100) : 0;
                    return (
                      <div key={cat.category} className="space-y-1">
                        <div className="flex items-center justify-between text-[12px]">
                          <span className="flex items-center gap-1.5 text-zinc-700">
                            <span>{cd.emoji}</span>
                            <span className="truncate">{cd.label}</span>
                          </span>
                          <span className="font-semibold text-zinc-900">{cat.count}</span>
                        </div>
                        <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-100">
                          <div
                            className="h-full rounded-full bg-zinc-800 transition-all"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </section>

        {/* ========== 第五行：规则命中榜 + 项目活跃度 ========== */}
        <section className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          {/* 规则命中榜 Top 8 */}
          <Card className="overflow-hidden">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">高频规则 Top 8</CardTitle>
              <CardDescription>命中数最高的规则，标注误报率</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              {statsBundle.rules.length === 0 ? (
                <div className="flex flex-col items-center gap-1.5 py-8 text-center"><FileX size={18} strokeWidth={1.5} className="text-zinc-300" /><span className="text-[13px] text-zinc-400">暂无问题</span></div>
              ) : (
                <div className="divide-y divide-zinc-100">
                  <div className="grid grid-cols-[minmax(0,2fr)_40px_50px] gap-2 py-1.5 text-[10px] font-medium uppercase text-zinc-400">
                    <div>规则</div>
                    <div className="text-right">命中</div>
                    <div className="text-right">误报</div>
                  </div>
                  {statsBundle.rules.slice(0, 8).map((rule) => {
                    const sev = severityDisplay(rule.severity_default);
                    const pct = Math.round(rule.fp_rate * 100);
                    return (
                      <div
                        key={rule.rule_id}
                        data-rule-id={rule.rule_id}
                        className="grid grid-cols-[minmax(0,2fr)_40px_50px] items-center gap-2 py-1.5 text-[12px]"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span title={sev.label}>{sev.emoji}</span>
                            <span className="truncate font-mono text-[11px] text-zinc-600">
                              {rule.rule_id}
                            </span>
                          </div>
                          <div className="truncate text-[11px] text-zinc-400">
                            {rule.title ?? '（规则已删除）'}
                          </div>
                        </div>
                        <div className="text-right font-semibold text-zinc-900">
                          {rule.finding_count}
                        </div>
                        <div className="text-right">
                          <span className={cn(
                            'rounded px-1 py-0.5 text-[10px]',
                            pct >= 30 ? 'bg-red-100 text-red-700' :
                            pct >= 10 ? 'bg-yellow-100 text-yellow-800' :
                            'bg-zinc-100 text-zinc-600'
                          )}>
                            {pct}%
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          {/* 项目活跃度 Top 6 */}
          <Card className="overflow-hidden">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">活跃项目 Top 6</CardTitle>
              <CardDescription>按审查数排序，展示问题量与 BLOCKER 数</CardDescription>
            </CardHeader>
            <CardContent className="pb-4">
              {statsBundle.projects.length === 0 ? (
                <div className="flex flex-col items-center gap-1.5 py-8 text-center"><FileX size={18} strokeWidth={1.5} className="text-zinc-300" /><span className="text-[13px] text-zinc-400">暂无项目</span></div>
              ) : (
                <div className="divide-y divide-zinc-100">
                  <div className="grid grid-cols-[minmax(0,1.5fr)_35px_35px] gap-2 py-1.5 text-[10px] font-medium uppercase text-zinc-400">
                    <div>项目</div>
                    <div className="text-right">审查</div>
                    <div className="text-right">问题</div>
                  </div>
                  {statsBundle.projects.slice(0, 6).map((p) => (
                    <div
                      key={p.project_id}
                      data-project-id={p.project_id}
                      className="grid grid-cols-[minmax(0,1.5fr)_35px_35px] items-center gap-2 py-1.5 text-[12px]"
                    >
                      <div className="truncate text-zinc-700">{p.project_name}</div>
                      <div className="text-right font-semibold text-zinc-900">{p.review_count}</div>
                      <div className="text-right">
                        <span className={cn(
                          p.blocker_count > 0 ? 'text-rose-600 font-medium' : 'text-zinc-500'
                        )}>
                          {p.finding_count}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
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
      <div className="space-y-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>已配置供应商</CardTitle>
              <CardDescription>{items.length} 个，其中 {enabledCount} 个已启用</CardDescription>
            </div>
            <Button size="sm" onClick={() => setProviderDialog({ mode: 'create' })}>
              新增供应商
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <EmptyState icon={Boxes} title="暂无模型供应商" action={<Button size="sm" onClick={() => setProviderDialog({ mode: 'create' })}>新增供应商</Button>} />
            ) : (
              items.map((provider) => (
                <ProviderListItem
                  key={provider.id}
                  provider={provider}
                  onSave={handleSaveProviderEdit}
                  onEdit={() => setProviderDialog({ mode: 'edit', data: provider })}
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
      <div className="space-y-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>审查规则</CardTitle>
              <CardDescription>{items.length} 条规则 · {enabledCount} 启用</CardDescription>
            </div>
            <Button size="sm" onClick={() => setRuleDialog({ mode: 'create' })}>
              新增规则
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <EmptyState icon={ListChecks} title="暂无审查规则" action={<Button size="sm" onClick={() => setRuleDialog({ mode: 'create' })}>新增规则</Button>} />
            ) : (
              items.map((rule) => (
                <div key={rule.id} className="flex items-center justify-between px-4 py-3 hover:bg-zinc-50 transition-colors border-b border-zinc-100 last:border-b-0">
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-zinc-900 truncate">
                      <span className="font-mono">{rule.rule_id}</span><span className="font-normal text-zinc-600"> {rule.title}</span>
                    </div>
                    <div className="text-[11px] text-zinc-500 mt-0.5 font-mono truncate">{rule.severity_default} · {truncate(rule.prompt_snippet, 60)}</div>
                    {rule.tags.length > 0 ? (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {rule.tags.map((tag) => (
                          <span key={tag} className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 text-[10px] font-medium">
                            {tag}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <UiBadge {...severityBadgeProps(rule.severity_default)}>{rule.severity_default}</UiBadge>
                    <UiBadge variant={rule.enabled ? 'success' : 'default'}>
                      <span className={cn('w-1.5 h-1.5 rounded-full', rule.enabled ? 'bg-emerald-500' : 'bg-zinc-400')} />
                      {rule.enabled ? '启用' : '停用'}
                    </UiBadge>
                    <Button variant="ghost" size="sm" type="button" onClick={() => setRuleDialog({ mode: 'edit', data: rule })}>编辑</Button>
                    <Button variant="destructive" size="sm" type="button" onClick={() => void handleDeleteRule(rule)}>删除</Button>
                  </div>
                </div>
              ))
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
      <div className="space-y-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>已接入项目</CardTitle>
              <CardDescription>{projectsPage?.items?.length ?? 0} 个项目 · {(projectsPage?.items ?? []).filter((p) => p.enabled).length} 启用</CardDescription>
            </div>
            <Button size="sm" onClick={() => setProjectDialog({ mode: 'create' })}>
              新增项目
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            {(projectsPage?.items ?? []).length === 0 ? (
              <EmptyState icon={FolderGit2} title="暂无 GitLab 项目" action={<Button size="sm" onClick={() => setProjectDialog({ mode: 'create' })}>新增项目</Button>} />
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
                  onEditProject={() => setProjectDialog({ mode: 'edit', data: project })}
                />
              ))
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  // 用户映射页：列表、弹窗、CRUD 全部封装在 UserMappingsPage 组件内，
  // 这里只透传已加载的项目列表。
  function renderUserMappings() {
    return <UserMappingsPage projects={projectsPage?.items ?? []} />;
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
              <EmptyState icon={ScrollText} title="暂无审查记录" description="当 GitLab MR 触发审查后，记录会显示在这里" />
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
    // PR-B：前端筛选（fp_status / 严重度 / 搜索），量级几百条内在前端做就够。
    const q = findingsSearch.trim().toLowerCase();
    const filtered = items.filter((finding) => {
      if (findingsFpFilter !== 'ALL') {
        const status = finding.fp_status || 'NONE';
        if (status !== findingsFpFilter) return false;
      }
      if (findingsSeverityFilter.size > 0) {
        const upper = (finding.severity ?? '').toUpperCase();
        if (!isKnownSeverity(upper) || !findingsSeverityFilter.has(upper as Severity)) {
          return false;
        }
      }
      if (q) {
        const haystack = `${finding.title ?? ''} ${finding.rule_id ?? ''} ${finding.file_path ?? ''}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
    return (
      <div className="space-y-4">
        {/* PR-B：筛选栏——误报状态单选、严重度多选、关键字搜索，全前端 filter。 */}
        <Card>
          <CardContent className="p-3 space-y-2">
            <div className="flex items-center flex-wrap gap-1.5">
              <span className="text-[11px] text-zinc-500 mr-1">误报状态：</span>
              {(['ALL', 'NONE', 'PENDING', 'CONFIRMED', 'REJECTED'] as const).map((option) => {
                const active = findingsFpFilter === option;
                const label = option === 'ALL'
                  ? '全部'
                  : option === 'NONE'
                    ? '未处理'
                    : option === 'PENDING'
                      ? '待审核'
                      : option === 'CONFIRMED'
                        ? '已确认'
                        : '已驳回';
                return (
                  <button
                    key={option}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setFindingsFpFilter(option)}
                    className={cn(
                      'inline-flex items-center gap-1 px-2 py-[3px] rounded-full border text-[12px] leading-none transition-colors',
                      active
                        ? 'bg-zinc-900 text-white border-zinc-900'
                        : 'bg-white text-zinc-700 border-zinc-300 hover:bg-zinc-50',
                    )}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            <div className="flex items-center flex-wrap gap-1.5">
              <span className="text-[11px] text-zinc-500 mr-1">严重度：</span>
              {SEVERITY_ORDER.map((sev) => {
                const disp = severityDisplay(sev);
                const active = findingsSeverityFilter.has(sev);
                return (
                  <button
                    key={sev}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setFindingsSeverityFilter((prev) => {
                      const next = new Set(prev);
                      if (next.has(sev)) next.delete(sev); else next.add(sev);
                      return next;
                    })}
                    className={cn(
                      'inline-flex items-center gap-1 px-2 py-[3px] rounded-full border text-[12px] leading-none transition-colors',
                      active
                        ? 'bg-zinc-900 text-white border-zinc-900'
                        : 'bg-white text-zinc-700 border-zinc-300 hover:bg-zinc-50',
                    )}
                  >
                    <span aria-hidden>{disp.emoji}</span>
                    <span>{disp.label}</span>
                  </button>
                );
              })}
            </div>
            <div>
              <input
                type="text"
                value={findingsSearch}
                onChange={(event) => setFindingsSearch(event.target.value)}
                placeholder="🔍 搜索标题 / rule_id / 文件路径"
                aria-label="搜索问题"
                className="w-full rounded-md border border-zinc-200 px-3 py-1.5 text-[13px] focus:outline-none focus:ring-2 focus:ring-indigo-200"
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>问题与误报</CardTitle>
              <CardDescription>{filtered.length} / {items.length} 条问题记录</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {filtered.length === 0 ? (
              <div className="p-6 text-center text-[13px] text-zinc-500">
                {items.length === 0 ? '暂无问题记录' : '当前筛选条件无匹配'}
              </div>
            ) : (
              filtered.map((finding) => {
                // fp_status 决定"标记误报"按钮是否可点：只有 NONE 才允许再标一次。
                // PENDING / CONFIRMED / REJECTED 都是终态或已入队，再点没意义。
                const fpBadge = fpStatusBadgeProps(finding.fp_status);
                // finding.status 徽章：仅 resolved / mr_closed 展示，open / 空不渲染。
                const statusBadge = statusBadgeProps(finding.status);
                const canMark = finding.fp_status === 'NONE';
                const markLabel = canMark
                  ? '标记误报'
                  : finding.fp_status === 'PENDING'
                    ? '已提交'
                    : '已处理';
                // hover title：告诉用户是谁 / 什么时候标的，快速溯源。
                const markTitle = finding.fp_marked_by
                  ? `已由 ${finding.fp_marked_by} 于 ${relativeTime(finding.fp_marked_at ?? undefined)} 标记`
                  : undefined;
                // MR 上下文行：project_name / mr_iid 若缺失走占位，mr_title 目前后端
                // 恒为 null（Review 表未落库），有值时前置显示，避免 UI 空跑。
                const mrIidLabel = finding.mr_iid ? `MR !${finding.mr_iid}` : 'MR !-';
                const projectLabel = finding.project_name ?? '未知项目';
                const timeLabel = relativeTime(finding.review_created_at ?? undefined);
                return (
                  <div key={finding.id} className="flex items-start justify-between px-4 py-3 border-b border-zinc-100 last:border-b-0 hover:bg-zinc-50 transition-colors">
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="text-[13px] font-medium text-zinc-900 truncate">{finding.title}</div>
                      <div className="text-[11px] text-zinc-500 truncate">
                        {finding.mr_title ? (
                          <>
                            <span className="text-zinc-700">{finding.mr_title}</span>
                            <span className="mx-1">·</span>
                          </>
                        ) : null}
                        <span>{projectLabel}</span>
                        <span className="mx-1">·</span>
                        <span className="font-mono">{mrIidLabel}</span>
                        {timeLabel ? (
                          <>
                            <span className="mx-1">·</span>
                            <span>{timeLabel}</span>
                          </>
                        ) : null}
                      </div>
                      <div className="text-[11px] text-zinc-500 font-mono truncate">{finding.file_path}:{finding.line_number ?? '-'} · {finding.rule_id}</div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <UiBadge {...severityBadgeProps(finding.severity)}>{finding.severity}</UiBadge>
                      {statusBadge ? <UiBadge variant={statusBadge.variant} className={statusBadge.className}>{statusBadge.label}</UiBadge> : null}
                      {fpBadge ? <UiBadge variant={fpBadge.variant} className={fpBadge.className}>{fpBadge.label}</UiBadge> : null}
                      <Button
                        variant="ghost"
                        size="sm"
                        type="button"
                        disabled={finding.status === 'resolved'}
                        title={finding.status === 'resolved' ? '该问题已标记为已解决' : '直接标记为已解决，不进入误报流程'}
                        onClick={() => openResolveDialog(finding)}
                      >
                        标记已解决
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        type="button"
                        disabled={!canMark}
                        title={markTitle}
                        onClick={() => openMarkDialog(finding)}
                      >
                        {markLabel}
                      </Button>
                    </div>
                  </div>
                );
              })
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  function renderFalsePositives() {
    const items = pendingFpPage?.items ?? [];
    return (
      <div className="space-y-4">
        {/* PR-B：顶部审核人/备注卡片已移除，改由 ReviewFalsePositiveDialog 内部收集。 */}
        <Card>
          <CardHeader>
            <div>
              <CardTitle>误报队列</CardTitle>
              <CardDescription>{items.length} 条待审核</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {items.length === 0 ? (
              <EmptyState icon={Filter} title="暂无待确认误报" description="开发者标记的误报会进入此队列" />
            ) : (
              items.map((finding) => {
                const projectLabel = finding.project_name ?? '未知项目';
                const mrLabel = finding.mr_iid ? `MR !${finding.mr_iid}` : 'MR !-';
                const markedRel = relativeTime(finding.fp_marked_at ?? undefined);
                return (
                  <div key={finding.id} className="flex items-start justify-between px-4 py-3 border-b border-zinc-100 last:border-b-0 hover:bg-zinc-50 transition-colors">
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="text-[13px] font-medium text-zinc-900 truncate">{finding.title}</div>
                      <div className="text-[11px] text-zinc-500 truncate">
                        <span>{projectLabel}</span>
                        <span className="mx-1">·</span>
                        <span className="font-mono">{mrLabel}</span>
                        <span className="mx-1">·</span>
                        <span className="font-mono">{finding.rule_id}</span>
                      </div>
                      <div className="text-[11px] text-zinc-500 font-mono truncate">
                        {finding.file_path}:{finding.line_number ?? '-'}
                      </div>
                      <div className="text-[11px] text-zinc-500 truncate">
                        提交人 <span className="text-zinc-700">{finding.fp_marked_by ?? '未知'}</span>
                        {markedRel ? (
                          <>
                            <span className="mx-1">·</span>
                            <span>{markedRel}</span>
                          </>
                        ) : null}
                      </div>
                      <div className="text-[12px] text-zinc-700 line-clamp-2 whitespace-pre-wrap break-words">
                        <span className="text-zinc-500">提交原因：</span>
                        {finding.fp_marked_reason ?? '（未填写）'}
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 ml-3">
                      <Button size="sm" type="button" onClick={() => openReviewDialog(finding, 'confirm')}>确认误报</Button>
                      <Button variant="secondary" size="sm" type="button" onClick={() => openReviewDialog(finding, 'reject')}>驳回</Button>
                    </div>
                  </div>
                );
              })
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  function renderNegativeExamples() {
    const items = negativeExamplesPage?.items ?? [];
    // rule_id 下拉候选：从已加载数据里去重，避免额外接口。keyword 搜索：rule_id / project_name / explanation / snippet。
    const ruleOptions = Array.from(new Set(items.map((it) => it.rule_id).filter(Boolean))).sort();
    const q = negSearch.trim().toLowerCase();
    const filtered = items.filter((example) => {
      if (negRuleFilter && example.rule_id !== negRuleFilter) return false;
      if (q) {
        const haystack = [
          example.rule_id ?? '',
          example.explanation ?? '',
          example.code_snippet ?? '',
        ].join(' ').toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
    return (
      <div className="space-y-4">
        <Card>
          <CardContent className="p-3 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] text-zinc-500">规则：</span>
              <select
                aria-label="按规则筛选"
                value={negRuleFilter}
                onChange={(event) => setNegRuleFilter(event.target.value)}
                className="h-8 rounded-md border border-zinc-200 bg-white px-2 text-[13px] focus:outline-none focus:ring-2 focus:ring-indigo-200"
              >
                <option value="">全部规则</option>
                {ruleOptions.map((rid) => (
                  <option key={rid} value={rid}>{rid}</option>
                ))}
              </select>
            </div>
            <div>
              <input
                type="text"
                value={negSearch}
                onChange={(event) => setNegSearch(event.target.value)}
                placeholder="🔍 搜索规则 / 项目 / 说明 / 片段"
                aria-label="搜索负样本"
                className="w-full rounded-md border border-zinc-200 px-3 py-1.5 text-[13px] focus:outline-none focus:ring-2 focus:ring-indigo-200"
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>负样本库</CardTitle>
              <CardDescription>
                {filtered.length} / {items.length} 条已批准
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {filtered.length === 0 ? (
              <div className="p-6 text-center text-[13px] text-zinc-500">
                {items.length === 0 ? '暂无负样本' : '当前筛选条件无匹配'}
              </div>
            ) : (
              filtered.map((example) => {
                const createdRel = relativeTime(example.created_at ?? undefined);
                return (
                  <div key={example.id} className="px-4 py-3 border-b border-zinc-100 last:border-b-0 space-y-2">
                    <div className="flex items-center gap-2 flex-wrap text-[11px] text-zinc-500">
                      <span className="font-mono text-zinc-700">{example.rule_id}</span>
                      {example.approved_by ? (
                        <>
                          <span>·</span>
                          <span>批准人 <span className="text-zinc-700">{example.approved_by}</span></span>
                        </>
                      ) : null}
                      {createdRel ? (
                        <>
                          <span>·</span>
                          <span>{createdRel}</span>
                        </>
                      ) : null}
                      {example.source_finding_id ? (
                        <>
                          <span>·</span>
                          <span className="font-mono">来源 finding #{example.source_finding_id}</span>
                        </>
                      ) : null}
                    </div>
                    <div className="text-[12px] font-mono bg-zinc-50 rounded p-2 whitespace-pre-wrap break-all">
                      {example.code_snippet}
                    </div>
                    {example.explanation ? (
                      <div className="text-[12px] text-zinc-600 whitespace-pre-wrap">{example.explanation}</div>
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
              <EmptyState icon={Cpu} title="暂无引擎配置" description="引擎配置会在首次启动时自动创建" />
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

  async function handleSaveGlobalPrompt() {
    setGlobalPromptSaving(true);
    setError(null);
    setMessage(null);
    try {
      await updateGlobalPrompt(globalPrompt);
      setMessage('全局提示词已保存。');
    } catch (caught) {
      handleCaughtError(caught);
    } finally {
      setGlobalPromptSaving(false);
    }
  }

  function renderGlobalPrompt() {
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>全局提示词</CardTitle>
              <CardDescription>
                这段提示词会被注入到每次 LLM 代码审查的 system prompt 开头，用于定义全局审查原则和风格。为空时不注入。
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="global-prompt-textarea">提示词内容</Label>
              <Textarea
                id="global-prompt-textarea"
                value={globalPrompt}
                onChange={(event) => setGlobalPrompt(event.target.value)}
                placeholder="例如：请特别关注安全性问题，优先审查输入验证、权限控制、SQL 注入等..."
                className="min-h-[320px] font-mono text-sm leading-relaxed"
              />
              <span className="block text-[11px] text-zinc-500">
                最长 50000 字符。修改后约 60 秒内生效（带缓存）。
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button onClick={handleSaveGlobalPrompt} disabled={globalPromptSaving}>
                {globalPromptSaving ? '保存中...' : '保存'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }
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

/** SVG 面积趋势图（参考 Vercel Analytics 风格）。 */
function TrendChart({ data }: { data: TimeseriesPoint[] }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const W = 640;
  const H = 160;
  const P = { top: 12, right: 12, bottom: 28, left: 32 };
  const cw = W - P.left - P.right;
  const ch = H - P.top - P.bottom;
  const max = Math.max(1, ...data.map((d) => d.review_count));
  const step = data.length > 1 ? cw / (data.length - 1) : cw;
  const pts = data.map((d, i) => ({
    x: P.left + i * step,
    y: P.top + ch - (d.review_count / max) * ch,
    d,
  }));
  const areaPath = pts.length > 0
    ? `M ${pts[0].x} ${P.top + ch} ` + pts.map((p) => `L ${p.x} ${p.y}`).join(' ') + ` L ${pts[pts.length - 1].x} ${P.top + ch} Z`
    : '';
  const linePath = pts.length > 0
    ? pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')
    : '';
  const yTicks = [0, Math.ceil(max / 2), max];
  const labelInterval = data.length <= 7 ? 1 : data.length <= 30 ? 5 : 10;

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 'auto' }}>
        <defs>
          <linearGradient id="trendArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#27272A" stopOpacity={0.12} />
            <stop offset="100%" stopColor="#27272A" stopOpacity={0} />
          </linearGradient>
        </defs>
        {/* Y 轴网格线 + 刻度 */}
        {yTicks.map((tick, i) => {
          const y = P.top + ch - (tick / max) * ch;
          return (
            <g key={i}>
              <line x1={P.left} y1={y} x2={W - P.right} y2={y} stroke="#F4F4F5" strokeWidth={1} />
              <text x={P.left - 6} y={y + 3} textAnchor="end" fontSize={9} fill="#A1A1AA">{tick}</text>
            </g>
          );
        })}
        {/* 面积 */}
        <path d={areaPath} fill="url(#trendArea)" />
        {/* 折线 */}
        <path d={linePath} fill="none" stroke="#27272A" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
        {/* BLOCKER 标记点 */}
        {pts.filter((p) => p.d.blocker_count > 0).map((p, i) => (
          <circle key={`b-${i}`} cx={p.x} cy={p.y} r={3.5} fill="#EF4444" stroke="white" strokeWidth={1.5} />
        ))}
        {/* hover 指示线 + 高亮点 */}
        {hovered !== null && pts[hovered] && (
          <g>
            <line x1={pts[hovered].x} y1={P.top} x2={pts[hovered].x} y2={P.top + ch} stroke="#D4D4D8" strokeWidth={1} strokeDasharray="3 3" />
            <circle cx={pts[hovered].x} cy={pts[hovered].y} r={4} fill="#27272A" stroke="white" strokeWidth={2} />
          </g>
        )}
        {/* X 轴日期标签 */}
        {data.map((d, i) => {
          if (i % labelInterval !== 0 && i !== data.length - 1) return null;
          const x = P.left + i * step;
          return (
            <text key={`l-${i}`} x={x} y={H - 8} textAnchor="middle" fontSize={9} fill="#A1A1AA" fontFamily="monospace">{d.date.slice(5)}</text>
          );
        })}
        {/* 透明 hover 区域 */}
        {pts.map((p, i) => (
          <rect key={`h-${i}`} x={p.x - step / 2} y={0} width={step} height={H} fill="transparent"
            onMouseEnter={() => setHovered(i)} onMouseLeave={() => setHovered(null)} />
        ))}
      </svg>
      {/* hover tooltip */}
      {hovered !== null && data[hovered] && (
        <div className="pointer-events-none absolute top-0 z-10 rounded-md border border-zinc-200 bg-white px-2 py-1 text-[11px] shadow-md"
          style={{ left: `${(pts[hovered].x / W) * 100}%`, transform: 'translateX(-50%)' }}>
          <div className="font-mono text-zinc-900">{data[hovered].date}</div>
          <div className="text-zinc-500">评审 {data[hovered].review_count} 次 · 发现 {data[hovered].finding_count} 个问题 · 其中 {data[hovered].blocker_count} 个 BLOCKER</div>
        </div>
      )}
    </div>
  );
}

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
  const allOk = apiOk && dbOk && (engines.length === 0 || enginesAllHealthy);

  const rows: Array<{ label: string; ok: boolean; value: string; version?: string }> = [
    { label: 'API 服务', ok: apiOk, value: apiOk ? '服务正常' : '服务异常', version: health?.version },
    { label: '数据库', ok: dbOk, value: dbOk ? '数据库正常' : '数据库异常' },
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
  onEdit: () => void;
};

function ProviderListItem({ provider, onSave, onEdit }: ProviderListItemProps) {
  const letter = provider.name.charAt(0).toUpperCase();
  const baseUrlShort = provider.base_url.replace(/^https?:\/\//, '').replace(/\/$/, '');

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
          <Button variant="ghost" size="sm" type="button" onClick={onEdit}>编辑</Button>
        </div>
      </div>
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
        <EmptyState icon={ScrollText} title="暂无审查记录" description="当 GitLab MR 触发审查后，记录会显示在这里" />
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
              {(() => {
                // PR #96：lifecycle_event 有值时优先渲染专属徽章，替代 review_mode 徽章。
                const lifecycleBadge = lifecycleEventBadgeProps(review.lifecycle_event);
                if (lifecycleBadge) {
                  return (
                    <UiBadge
                      variant={lifecycleBadge.variant}
                      className={cn(lifecycleBadge.className, 'h-4 px-1.5 text-[10px]')}
                      title={lifecycleBadge.title}
                    >
                      {lifecycleBadge.label}
                    </UiBadge>
                  );
                }
                if (review.review_mode && review.review_mode !== 'full') {
                  // PR #89：首页面板只在非 full 时展示紧凑徽章，避免"全量"占位噪声。
                  const modeBadge = reviewModeBadgeProps(review.review_mode);
                  return (
                    <UiBadge
                      variant={modeBadge.variant}
                      className={cn(modeBadge.className, 'h-4 px-1.5 text-[10px]')}
                      title={modeBadge.title}
                    >
                      {modeBadge.label}
                    </UiBadge>
                  );
                }
                return null;
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
  onEditProject: () => void;
};

function ProjectCard({ project, providerOptions, rules, onSavePolicies, onSaveProject, onDeleteProject, onEditProject }: ProjectCardProps) {
    const [expanded, setExpanded] = useState(false);
    const [deleting, setDeleting] = useState(false);
    // 负样本提示词弹窗：仅在打开时渲染，关闭即销毁。
    const [negativePromptOpen, setNegativePromptOpen] = useState(false);
    const letter = project.name.charAt(0).toUpperCase();

    async function handleDelete() {
      if (deleting) {
        return;
      }
      if (!confirm(`确定要删除项目「${project.name}」吗？删除后该项目的 MR 审查将无法使用。`)) {
        return;
      }
      try {
        setDeleting(true);
        await onDeleteProject(project.id);
      } finally {
        setDeleting(false);
      }
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
            <Button variant="ghost" size="sm" type="button" onClick={onEditProject}>编辑</Button>
            <Button variant="destructive" size="sm" type="button" disabled={deleting} onClick={() => void handleDelete()}>{deleting ? '删除中…' : '删除'}</Button>
            <Button variant="ghost" size="sm" type="button" onClick={() => setNegativePromptOpen(true)}>负样本提示词</Button>
            <Button variant="secondary" size="sm" type="button" onClick={() => setExpanded((prev) => !prev)}>
              {expanded ? '收起策略' : '展开策略'}
            </Button>
          </div>
        </div>
        {expanded ? (
          <div className="px-4 pb-4 bg-zinc-50 border-t border-zinc-100">
            <BlockPolicyTable projectId={project.id} policies={project.block_policies} onSave={onSavePolicies} />
          </div>
        ) : null}
        {negativePromptOpen ? (
          <NegativePromptDialog
            open={negativePromptOpen}
            onClose={() => setNegativePromptOpen(false)}
            projectId={project.id}
            projectName={project.name}
          />
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
      {items.length === 0 ? <div className="flex flex-col items-center gap-1.5 py-6 text-center"><Settings size={18} strokeWidth={1.5} className="text-zinc-300" /><span className="text-[13px] text-zinc-400">暂无阻断策略，点击下方按钮添加</span></div> : null}
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
            {review.lifecycle_event ? (
              // PR #96：lifecycle 记账 review 的 rules_used / engine_used /
              // parent_review_id 都是 NULL/空，展示"辅助信息占位符"或引擎徽章都会
              // 误导用户，改成一行灰体小字说明这是记账事件。
              <div className="mt-1 text-[10px] text-zinc-400">MR 生命周期事件（不消耗 engine）</div>
            ) : (review.rules_used ?? []).length > 0 || review.engine_used || review.parent_review_id ? (
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
                {review.parent_review_id ? (
                  // PR #89：串链提示。本 PR 不做跳转（要额外路由），只显示 slice(0,7)，
                  // 完整 parent_id 放到 title 里供开发者排查。
                  <span
                    className="inline-flex items-center rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 text-[10px] font-mono text-zinc-500"
                    title={`接续自评审：${review.parent_review_id}`}
                  >
                    ↩ 接续自 {review.parent_review_id.slice(0, 7)}
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
            // PR #96：lifecycle_event 有值时优先渲染专属徽章，替代 review_mode 徽章。
            const lifecycleBadge = lifecycleEventBadgeProps(review.lifecycle_event);
            if (lifecycleBadge) {
              return (
                <UiBadge
                  variant={lifecycleBadge.variant}
                  className={lifecycleBadge.className}
                  title={lifecycleBadge.title}
                >
                  {lifecycleBadge.label}
                </UiBadge>
              );
            }
            // PR #89：review_mode 徽章紧挨 status 徽章展示。老数据 review_mode 缺失
            // 走 helper 的 'full' fallback，展示"全量"灰色徽章，避免出现空白。
            const modeBadge = reviewModeBadgeProps(review.review_mode, review.base_sha);
            return (
              <UiBadge variant={modeBadge.variant} className={modeBadge.className} title={modeBadge.title}>
                {modeBadge.label}
              </UiBadge>
            );
          })()}
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
          {!loadingFindings && findings !== null && findings.length === 0 ? (
            // PR #96：lifecycle 记账 review 展开时 findings 必然空，把"暂无问题"
            // 换成明确说明——这条不是审查，是记账。
            review.lifecycle_event ? (
              <div className="py-3 text-[13px] text-zinc-500 text-center">MR 生命周期事件，未产生新的审查内容</div>
            ) : (
              <div className="flex flex-col items-center gap-1.5 py-6 text-center"><FileX size={18} strokeWidth={1.5} className="text-zinc-300" /><span className="text-[13px] text-zinc-400">暂无问题</span></div>
            )
          ) : null}
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
 * finding.fp_status → UiBadge props（+ label）。
 *
 * - ``NONE``：返回 null——列表项默认不渲染徽章，视觉更干净；调用方自行判断。
 * - ``PENDING``：琥珀色「误报待审」。
 * - ``CONFIRMED``：绿色「已确认误报」。
 * - ``REJECTED``：玫红色「误报驳回」。
 * - 未知值：中性灰徽章 + 原字符串，防止后端偷偷加新状态但前端不更新。
 *
 * export 出去是给单测 assert 各分支输出的。
 */
export function fpStatusBadgeProps(
  status: string,
):
  | { variant: 'default'; className: string; label: string }
  | null {
  if (status === 'NONE') return null;
  if (status === 'PENDING') {
    return {
      variant: 'default',
      className: 'border-amber-200 bg-amber-50 text-amber-700',
      label: '误报待审',
    };
  }
  if (status === 'CONFIRMED') {
    return {
      variant: 'default',
      className: 'border-emerald-200 bg-emerald-50 text-emerald-700',
      label: '已确认误报',
    };
  }
  if (status === 'REJECTED') {
    return {
      variant: 'default',
      className: 'border-rose-200 bg-rose-50 text-rose-700',
      label: '误报驳回',
    };
  }
  return {
    variant: 'default',
    className: 'border-zinc-200 bg-zinc-50 text-zinc-600',
    label: status,
  };
}

/**
 * finding.status → UiBadge props（+ label）。
 *
 * 与 fp_status 分开：这条链路展示的是"生命周期"（open / resolved / mr_closed），
 * 而不是"发现是否为误报"。
 *
 * - ``open`` / null / undefined：返回 null——默认活跃状态不渲染徽章，避免噪声。
 * - ``resolved``：绿色「已修复」（包含 MR 合并进主线导致的收敛）。
 * - ``mr_closed``：灰色「MR 已关闭」——所属 MR 被关闭而非合并，这些 finding 已作废。
 * - 未知值：中性灰兜底 + 原字符串，帮助发现后端偷加新状态但前端漏更新。
 *
 * export 出去是给单测 assert 各分支输出的。
 */
export function statusBadgeProps(
  status: string | null | undefined,
):
  | { variant: 'default'; className: string; label: string }
  | null {
  if (!status || status === 'open') return null;
  if (status === 'resolved') {
    return {
      variant: 'default',
      className: 'border-emerald-200 bg-emerald-50 text-emerald-700',
      label: '已修复',
    };
  }
  if (status === 'mr_closed') {
    return {
      variant: 'default',
      className: 'border-zinc-200 bg-zinc-100 text-zinc-600',
      label: 'MR 已关闭',
    };
  }
  return {
    variant: 'default',
    className: 'border-zinc-200 bg-zinc-50 text-zinc-600',
    label: status,
  };
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

/**
 * review_mode → UiBadge props（PR #89 增量审查串链的 UI 徽章）。
 *
 * - ``full`` 或 undefined（老数据、以及后端偶发字段缺失时的防御分支）：中性灰色，「全量」；
 * - ``incremental``：sky 蓝色，「增量」，配合 base_sha 提示前一次 push；
 * - ``reuse``：violet 紫色，「复用」，标注这次结果直接沿用上一次同 commit 的审查；
 * - 未知值：仍按中性灰渲染但 label 显示原字符串，帮前端及时发现后端加了新 mode 却没更新 UI。
 *
 * `baseSha` 参数只用于 ``incremental`` 分支组装 title；老数据可能为空，需兜底。
 */
export function reviewModeBadgeProps(
  mode: string | null | undefined,
  baseSha?: string | null,
): { variant: 'default'; className: string; label: string; title?: string } {
  if (mode === 'incremental') {
    // 老数据 base_sha 可能缺失；title 兜底给个通用说明，不硬拼接会产生 "undefined"。
    const shaSuffix = baseSha ? `相较上次 push: ${baseSha.slice(0, 7)}` : '相较上次 push 的增量审查';
    return {
      variant: 'default',
      className: 'border-sky-200 bg-sky-50 text-sky-700',
      label: '增量',
      title: shaSuffix,
    };
  }
  if (mode === 'reuse') {
    return {
      variant: 'default',
      className: 'border-violet-200 bg-violet-50 text-violet-700',
      label: '复用',
      title: '复用自上一次同 commit 的审查',
    };
  }
  if (mode && mode !== 'full') {
    // 未知模式：中性徽章 + 原字符串，便于开发环境显眼地发现。
    return {
      variant: 'default',
      className: 'border-zinc-200 bg-zinc-50 text-zinc-600',
      label: mode,
      title: `未知 review_mode：${mode}`,
    };
  }
  return {
    variant: 'default',
    className: 'border-zinc-200 bg-zinc-50 text-zinc-600',
    label: '全量',
  };
}

/**
 * review.lifecycle_event → UiBadge props。
 *
 * MR 生命周期事件（close / merge）的记账 review 在 UI 上不该看起来像普通审查。
 * 有 lifecycle_event 时优先展示这个徽章，替代（不并列）review_mode 徽章。
 *
 * - mr_closed → 灰色「MR 已关闭」
 * - mr_merged → 天蓝色「MR 已合并」
 * - null / undefined → null（调用方自己走 review_mode 徽章）
 */
export function lifecycleEventBadgeProps(
  event: string | null | undefined,
): { variant: 'default'; className: string; label: string; title: string } | null {
  if (event === 'mr_closed') {
    return {
      variant: 'default',
      className: 'border-zinc-300 bg-zinc-100 text-zinc-700',
      label: 'MR 已关闭',
      title: 'MR 关闭事件的生命周期记账，涉及的 finding 已标记为 mr_closed',
    };
  }
  if (event === 'mr_merged') {
    return {
      variant: 'default',
      className: 'border-sky-200 bg-sky-50 text-sky-700',
      label: 'MR 已合并',
      title: 'MR 合并事件的生命周期记账，涉及的 finding 已标记为 resolved',
    };
  }
  return null;
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

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return '未知错误';
}

export default App;
