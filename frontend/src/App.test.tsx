import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import App, { fpStatusBadgeProps, lifecycleEventBadgeProps, reviewModeBadgeProps, statusBadgeProps } from './App';

type MockResponse = {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
};

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

function mockFetch(handler: (url: string, init?: RequestInit) => Promise<MockResponse>): void {
  globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    return handler(url, init) as Promise<Response>;
  });
}

function jsonResponse(body: unknown, ok = true, status = 200): MockResponse {
  return {
    ok,
    status,
    json: async () => body,
  };
}

async function loginAsAdmin(): Promise<void> {
  await userEvent.type(screen.getByLabelText('管理员账号'), 'admin');
  await userEvent.type(screen.getByLabelText('管理员密码'), 'admin');
  await userEvent.click(screen.getByRole('button', { name: '登录' }));
  await waitFor(() => expect(screen.getByText('管理台已登录。')).toBeInTheDocument());
}

describe('MVP 管理台', () => {
  it('登录后保存管理 Token，并给管理 API 注入 Authorization 请求头', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    mockFetch(async (url, init) => {
      calls.push({ url, init });
      if (url === '/health') {
        return jsonResponse({ status: 'ok', version: '0.1.0-dev', db: 'ok' });
      }
      if (url === '/api/auth/login') {
        return jsonResponse({ access_token: 'admin-token', token_type: 'bearer', expires_in: 86400 });
      }
      if (url === '/api/engines') {
        const headers = init?.headers as Record<string, string> | undefined;
        if (headers?.Authorization !== 'Bearer admin-token') {
          return jsonResponse({ detail: 'Invalid admin token' }, false, 401);
        }
        return jsonResponse([]);
      }
      if (url === '/api/providers') {
        const headers = init?.headers as Record<string, string> | undefined;
        if (headers?.Authorization !== 'Bearer admin-token') {
          return jsonResponse({ detail: 'Invalid admin token' }, false, 401);
        }
        return jsonResponse({ items: [], total: 0, limit: 50, offset: 0 });
      }
      return jsonResponse({ detail: 'not found' }, false, 404);
    });

    render(<MemoryRouter><App /></MemoryRouter>);

    expect(await screen.findByText('管理台登录')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('管理员账号'), 'admin');
    await userEvent.type(screen.getByLabelText('管理员密码'), 'admin');
    await userEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => expect(screen.getByText('管理台已登录。')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: '模型供应商' }));
    await waitFor(() => expect(calls.some((call) => call.url === '/api/providers')).toBe(true));

    const engineCall = calls.find((call) => call.url === '/api/engines');
    const providerCall = calls.find((call) => call.url === '/api/providers');
    expect(engineCall?.init?.headers).toMatchObject({ Authorization: 'Bearer admin-token' });
    expect(providerCall?.init?.headers).toMatchObject({ Authorization: 'Bearer admin-token' });
  });

  it('展示健康状态、引擎状态，并自动拉取最近审查记录', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    mockFetch(async (url, init) => {
      calls.push({ url, init });
      if (url === '/health') {
        return jsonResponse({ status: 'ok', version: '0.1.0-dev', db: 'ok' });
      }
      if (url === '/api/auth/login') {
        return jsonResponse({ access_token: 'admin-token', token_type: 'bearer', expires_in: 86400 });
      }
      if (url === '/api/engines') {
        const headers = init?.headers as Record<string, string> | undefined;
        if (headers?.Authorization !== 'Bearer admin-token') {
          return jsonResponse({ detail: 'Invalid admin token' }, false, 401);
        }
        return jsonResponse([
          {
            name: 'llm-direct',
            supports_feedback: false,
            requires_repo_clone: false,
            healthy: true,
            health_status: 'ok',
          },
        ]);
      }
      if (url === '/api/reviews/recent') {
        // 最近审查改走 adminFetch（手动触发表单已移除），校验 Authorization。
        const headers = init?.headers as Record<string, string> | undefined;
        if (headers?.Authorization !== 'Bearer admin-token') {
          return jsonResponse({ detail: 'Invalid admin token' }, false, 401);
        }
        return jsonResponse([
          {
            review_id: '00000000-0000-0000-0000-000000000001',
            project_id: 123,
            project_path: 'group/demo',
            mr_iid: 7,
            title: '修复支付回调',
            web_url: 'https://gitlab.example.com/group/demo/-/merge_requests/7',
            status: 'done',
            has_blocker: true,
            finding_count: 3,
            blocker_count: 1,
            policy_applied: 'master -> BLOCKER',
            review_url: 'https://gitlab.example.com/group/demo/-/merge_requests/7#note_99',
            created_at: '2026-07-01T08:00:00Z',
            engine_used: 'llm-direct',
          },
          {
            // engine_error 场景：AI 引擎调用失败，policy 未阻断 → 应显示"引擎异常"，不是"通过"
            review_id: '00000000-0000-0000-0000-000000000002',
            project_id: 123,
            project_path: 'group/demo',
            mr_iid: 8,
            title: '主 LLM 超时',
            web_url: 'https://gitlab.example.com/group/demo/-/merge_requests/8',
            status: 'engine_error',
            has_blocker: false,
            finding_count: 0,
            blocker_count: 0,
            policy_applied: 'feature/* -> NONE',
            review_url: null,
            created_at: '2026-07-01T09:00:00Z',
            engine_used: 'llm-direct',
          },
          {
            // engine_error + policy 阻断 → "审查失败"（destructive）
            review_id: '00000000-0000-0000-0000-000000000003',
            project_id: 123,
            project_path: 'group/demo',
            mr_iid: 9,
            title: '主 LLM 超时（master）',
            web_url: null,
            status: 'engine_error',
            has_blocker: true,
            finding_count: 0,
            blocker_count: 1,
            policy_applied: 'master -> ENGINE_ERROR_ONLY',
            review_url: null,
            created_at: '2026-07-01T10:00:00Z',
            engine_used: 'llm-direct',
          },
        ]);
      }
      // 首页 dashboard 会并发拉审查记录 / 待处理误报 / 统计接口，全给空数据，
      // 否则 Promise.all 整体失败会导致最近审查面板不渲染。
      if (url === '/api/reviews/records' || url === '/api/false-positives/pending') {
        return jsonResponse({ items: [], total: 0, limit: 50, offset: 0 });
      }
      if (url.startsWith('/api/stats/')) {
        return jsonResponse(url.includes('overview') ? {} : []);
      }
      return jsonResponse({ detail: 'not found' }, false, 404);
    });

    render(<MemoryRouter><App /></MemoryRouter>);

    expect(await screen.findByText('AI Code Reviewer')).toBeInTheDocument();
    await loginAsAdmin();
    expect(await screen.findByText('服务正常')).toBeInTheDocument();
    // 引擎名同时出现在系统状态卡和最近审查面板，数量 >= 1 即可。
    expect(screen.getAllByText('llm-direct').length).toBeGreaterThan(0);

    // 首页最近审查面板：登录后自动经 adminFetch 拉取（不再有手动触发表单）。
    expect(await screen.findByText('修复支付回调')).toBeInTheDocument();
    expect(screen.getByText('阻断')).toBeInTheDocument();
    // engine_error + policy 允许合并 → "引擎异常"，不能被渲染为 "通过"。
    expect(screen.getByText('引擎异常')).toBeInTheDocument();
    // engine_error + policy 阻断 → "审查失败"。
    expect(screen.getByText('审查失败')).toBeInTheDocument();
    // 关键回归：任何 engine_error 行都不该冒充 "通过"。
    expect(screen.queryByText('通过')).not.toBeInTheDocument();
    // Issue #76：最近审查面板应展示引擎徽章。
    expect(await screen.findAllByText('llm-direct')).not.toHaveLength(0);
    // 最近审查走 adminFetch，应带 Authorization 头。
    const recentCall = calls.find((call) => call.url === '/api/reviews/recent');
    expect(recentCall?.init?.headers).toMatchObject({ Authorization: 'Bearer admin-token' });
  });

  it('可展开项目卡片、拖拽排序阻断策略并保存', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const projectId = '00000000-0000-0000-0000-000000000001';
    const project = {
      id: projectId,
      name: 'demo-project',
      gitlab_project_id: '123',
      gitlab_base_url: 'https://gitlab.example.com',
      gitlab_access_token: '****',
      webhook_secret: '****',
      engine_id: null,
      provider_id: null,
      enabled: true,
      default_block_severity: 'BLOCKER',
      timeout_seconds: 300,
      max_files: 50,
      ignore_paths: null,
      rules: [],
      block_policies: [
        {
          id: '00000000-0000-0000-0000-0000000000a1',
          project_id: projectId,
          branch_pattern: 'master',
          block_severity: 'BLOCKER',
          block_on_engine_error: false,
          require_all_resolved: false,
          priority: 1,
          created_at: '2026-07-01T00:00:00Z',
          updated_at: '2026-07-01T00:00:00Z',
        },
        {
          id: '00000000-0000-0000-0000-0000000000a2',
          project_id: projectId,
          branch_pattern: 'release/*',
          block_severity: 'WARNING',
          block_on_engine_error: false,
          require_all_resolved: false,
          priority: 2,
          created_at: '2026-07-01T00:00:00Z',
          updated_at: '2026-07-01T00:00:00Z',
        },
      ],
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    };
    mockFetch(async (url, init) => {
      calls.push({ url, init });
      if (url === '/health') {
        return jsonResponse({ status: 'ok', version: '0.1.0-dev', db: 'ok' });
      }
      if (url === '/api/auth/login') {
        return jsonResponse({ access_token: 'admin-token', token_type: 'bearer', expires_in: 86400 });
      }
      if (url === '/api/engines') {
        return jsonResponse([]);
      }
      if (url === '/api/projects') {
        return jsonResponse({ items: [project], total: 1, limit: 50, offset: 0 });
      }
      if (url === '/api/engines/configs') {
        return jsonResponse({ items: [], total: 0, limit: 50, offset: 0 });
      }
      if (url === '/api/rules' || url.startsWith('/api/rules?')) {
        return jsonResponse({ items: [], total: 0, limit: 50, offset: 0 });
      }
      if (url.startsWith('/api/projects/') && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { block_policies: Array<Record<string, unknown>> };
        return jsonResponse({
          ...project,
          block_policies: body.block_policies.map((policy, index) => ({
            ...policy,
            id: `bp-${index + 1}`,
            project_id: projectId,
            created_at: '2026-07-01T00:00:00Z',
            updated_at: '2026-07-01T00:00:00Z',
          })),
        });
      }
      return jsonResponse({ detail: 'not found' }, false, 404);
    });

    const { container } = render(<MemoryRouter><App /></MemoryRouter>);
    await loginAsAdmin();
    await userEvent.click(screen.getByRole('button', { name: 'GitLab 项目' }));
    await screen.findByText('demo-project');

    await userEvent.click(screen.getByRole('button', { name: '展开策略' }));
    const branchInputs = await screen.findAllByPlaceholderText('如 master 或 release/*');
    expect(branchInputs).toHaveLength(2);
    expect(branchInputs[0]).toHaveValue('master');
    expect(branchInputs[1]).toHaveValue('release/*');

    const rows = container.querySelectorAll('.policy-row');
    const handles = container.querySelectorAll('.drag-handle');
    expect(rows).toHaveLength(2);
    // 将第二条策略（release/*）拖到第一条（master）之前
    fireEvent.dragStart(handles[1]);
    fireEvent.dragOver(rows[0]);
    fireEvent.drop(rows[0]);

    const reordered = screen.getAllByPlaceholderText('如 master 或 release/*');
    expect(reordered[0]).toHaveValue('release/*');
    expect(reordered[1]).toHaveValue('master');

    await userEvent.click(screen.getByRole('button', { name: '保存策略' }));

    const patchCall = await waitFor(() => {
      const found = calls.find(
        (call) => call.url.startsWith('/api/projects/') && call.init?.method === 'PATCH',
      );
      expect(found).toBeTruthy();
      return found;
    });
    const body = JSON.parse(String(patchCall?.init?.body)) as {
      block_policies: Array<Record<string, unknown>>;
    };
    expect(body.block_policies).toEqual([
      expect.objectContaining({ branch_pattern: 'release/*', block_severity: 'WARNING', priority: 1 }),
      expect.objectContaining({ branch_pattern: 'master', block_severity: 'BLOCKER', priority: 2 }),
    ]);
  });

  // 项目级 commit 审查配置：编辑弹窗回填 + 开关联动数字输入 + 提交透传。
  it('编辑项目弹窗可配置 commit 推送审查并随 PATCH 提交', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const projectId = '00000000-0000-0000-0000-00000000000c';
    const project = {
      id: projectId,
      name: 'commit-review-project',
      gitlab_project_id: '123',
      gitlab_base_url: 'https://gitlab.example.com',
      gitlab_access_token: '****',
      webhook_secret: '****',
      engine_id: null,
      provider_id: null,
      enabled: true,
      default_block_severity: 'BLOCKER',
      timeout_seconds: 300,
      max_files: 50,
      commit_review_enabled: true,
      commit_review_max_per_push: 5,
      ignore_paths: null,
      rules: [],
      block_policies: [],
      notification_channels: [],
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    };
    mockFetch(async (url, init) => {
      calls.push({ url, init });
      if (url === '/health') {
        return jsonResponse({ status: 'ok', version: '0.1.0-dev', db: 'ok' });
      }
      if (url === '/api/auth/login') {
        return jsonResponse({ access_token: 'admin-token', token_type: 'bearer', expires_in: 86400 });
      }
      if (url === '/api/engines' || url === '/api/engines/configs') {
        return jsonResponse(url === '/api/engines' ? [] : { items: [], total: 0, limit: 50, offset: 0 });
      }
      if (url === '/api/providers') {
        return jsonResponse({ items: [], total: 0, limit: 50, offset: 0 });
      }
      // fetchRules 会带 ?limit=100&offset=0，用 startsWith 匹配。
      if (url.startsWith('/api/rules')) {
        return jsonResponse({ items: [], total: 0, limit: 50, offset: 0 });
      }
      if (url === '/api/projects') {
        return jsonResponse({ items: [project], total: 1, limit: 50, offset: 0 });
      }
      if (url === `/api/projects/${projectId}` && init?.method === 'PATCH') {
        return jsonResponse(project);
      }
      if (
        url === '/api/reviews/records' ||
        url === '/api/reviews/recent' ||
        url === '/api/false-positives/pending'
      ) {
        return url === '/api/reviews/recent'
          ? jsonResponse([])
          : jsonResponse({ items: [], total: 0, limit: 50, offset: 0 });
      }
      if (url.startsWith('/api/stats/')) {
        return jsonResponse(url.includes('overview') ? {} : []);
      }
      return jsonResponse({ detail: 'not found' }, false, 404);
    });

    render(<MemoryRouter><App /></MemoryRouter>);
    await loginAsAdmin();
    await userEvent.click(screen.getByRole('button', { name: 'GitLab 项目' }));

    // 打开编辑弹窗：commit 审查字段按项目配置回填。
    await userEvent.click(await screen.findByRole('button', { name: '编辑' }));
    const toggle = await screen.findByLabelText('启用 commit 推送审查');
    expect(toggle).toBeChecked();
    const maxInput = screen.getByLabelText('单次推送最多审查 commit 数 (兼容保留)');
    expect(maxInput).toHaveValue(5);
    expect(maxInput).toBeEnabled();

    // 关闭开关后，数字输入置灰 disabled。
    await userEvent.click(toggle);
    expect(maxInput).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    const patchCall = await waitFor(() => {
      const found = calls.find(
        (call) => call.url === `/api/projects/${projectId}` && call.init?.method === 'PATCH',
      );
      expect(found).toBeTruthy();
      return found;
    });
    expect(JSON.parse(String(patchCall?.init?.body))).toMatchObject({
      commit_review_enabled: false,
      commit_review_max_per_push: 5,
    });
  });

  // 用户映射页：导航可达 + 列表渲染 + 弹窗新增（钉钉 @MR 创建人配置入口）。
  it('用户映射页可查看列表并通过弹窗新增映射', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const projectId = '00000000-0000-0000-0000-0000000000aa';
    const project = {
      id: projectId,
      name: 'demo-project',
      gitlab_project_id: '123',
      gitlab_base_url: 'https://gitlab.example.com',
      gitlab_access_token: '****',
      webhook_secret: '****',
      engine_id: null,
      provider_id: null,
      enabled: true,
      default_block_severity: 'BLOCKER',
      timeout_seconds: 300,
      max_files: 50,
      ignore_paths: null,
      rules: [],
      block_policies: [],
      notification_channels: [],
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    };
    const mapping = {
      id: '00000000-0000-0000-0000-0000000000bb',
      project_id: projectId,
      gitlab_username: 'alice',
      dingtalk_mobile: '13800000000',
      dingtalk_userid: null,
      display_name: 'Alice',
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-01T00:00:00Z',
    };
    mockFetch(async (url, init) => {
      calls.push({ url, init });
      if (url === '/health') {
        return jsonResponse({ status: 'ok', version: '0.1.0-dev', db: 'ok' });
      }
      if (url === '/api/auth/login') {
        return jsonResponse({ access_token: 'admin-token', token_type: 'bearer', expires_in: 86400 });
      }
      if (url === '/api/engines') {
        return jsonResponse([]);
      }
      if (url === '/api/projects') {
        return jsonResponse({ items: [project], total: 1, limit: 50, offset: 0 });
      }
      if (url === `/api/projects/${projectId}/user-mappings` && (!init || init.method === undefined || init.method === 'GET')) {
        return jsonResponse([mapping]);
      }
      if (url === `/api/projects/${projectId}/user-mappings` && init?.method === 'POST') {
        return jsonResponse({ ...mapping, id: '00000000-0000-0000-0000-0000000000cc' }, true, 201);
      }
      return jsonResponse({ detail: 'not found' }, false, 404);
    });

    render(<MemoryRouter><App /></MemoryRouter>);
    await loginAsAdmin();
    await userEvent.click(screen.getByRole('button', { name: '用户映射' }));

    // 列表渲染已有映射。
    expect(await screen.findByText('alice')).toBeInTheDocument();
    expect(screen.getByText('13800000000')).toBeInTheDocument();

    // 打开新增弹窗并提交。
    await userEvent.click(screen.getByRole('button', { name: '+ 添加映射' }));
    await userEvent.type(await screen.findByLabelText('GitLab 用户名'), 'bob');
    await userEvent.type(screen.getByLabelText('钉钉手机号'), '13900000000');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    const createCall = await waitFor(() => {
      const found = calls.find((call) => call.url.endsWith('/user-mappings') && call.init?.method === 'POST');
      expect(found).toBeTruthy();
      return found;
    });
    expect(JSON.parse(String(createCall?.init?.body))).toMatchObject({
      gitlab_username: 'bob',
      dingtalk_mobile: '13900000000',
    });
  });
})

// PR #89 增量审查串链：helper 层单元测试。
// 只测 pure function 输出，不做组件级渲染——保证徽章颜色 / label / title 与设计
// 契约一致，也确保未知 mode 会被兜底展示（防止后端偷偷加新 mode 但前端不更新）。
describe('reviewModeBadgeProps', () => {
  it('full 与 undefined 都返回中性灰的"全量"徽章', () => {
    const full = reviewModeBadgeProps('full');
    const missing = reviewModeBadgeProps(undefined);
    const nullish = reviewModeBadgeProps(null);
    expect(full.label).toBe('全量');
    expect(full.variant).toBe('default');
    expect(full.className).toContain('bg-zinc-50');
    // 未传 title：不追加提示。
    expect(full.title).toBeUndefined();
    expect(missing).toEqual(full);
    expect(nullish).toEqual(full);
  });

  it('incremental 返回 sky 蓝徽章，且 title 携带 base_sha 前 7 位', () => {
    const withBase = reviewModeBadgeProps('incremental', 'deadbeefcafebabe1234');
    expect(withBase.label).toBe('增量');
    expect(withBase.className).toContain('bg-sky-50');
    expect(withBase.className).toContain('text-sky-700');
    // title 兜住 base_sha 前 7 位（不 hardcode "deadbeef" 全长）。
    expect(withBase.title).toBe('相较上次 push: deadbee');
  });

  it('incremental 但 base_sha 缺失时 title 兜底为通用说明，不会拼出 "undefined"', () => {
    const noBase = reviewModeBadgeProps('incremental');
    expect(noBase.label).toBe('增量');
    // 不该出现 "undefined"（否则说明 helper 没兜底）。
    expect(noBase.title).not.toContain('undefined');
    expect(noBase.title).toBe('相较上次 push 的增量审查');
  });

  it('reuse 返回 violet 紫徽章，title 说明"复用自上一次"', () => {
    const reuse = reviewModeBadgeProps('reuse');
    expect(reuse.label).toBe('复用');
    expect(reuse.className).toContain('bg-violet-50');
    expect(reuse.className).toContain('text-violet-700');
    expect(reuse.title).toBe('复用自上一次同 commit 的审查');
  });

  it('未知 mode 保留原字符串并给出显眼 title，帮助发现漏更新', () => {
    const weird = reviewModeBadgeProps('partial');
    expect(weird.label).toBe('partial');
    expect(weird.title).toContain('未知 review_mode');
    expect(weird.title).toContain('partial');
  });
});

// PR #96：MR 生命周期事件徽章（close / merge webhook 触发的记账 review 专用）。
// 只测 pure function 输出，验证颜色 / label / title 契约，以及 null / undefined
// 兜底返回 null（调用方走 review_mode 徽章）。
describe('lifecycleEventBadgeProps', () => {
  it('mr_closed 返回灰色「MR 已关闭」徽章', () => {
    const closed = lifecycleEventBadgeProps('mr_closed');
    expect(closed).not.toBeNull();
    expect(closed!.label).toBe('MR 已关闭');
    expect(closed!.variant).toBe('default');
    expect(closed!.className).toContain('bg-zinc-100');
    expect(closed!.className).toContain('text-zinc-700');
    expect(closed!.title).toContain('mr_closed');
  });

  it('mr_merged 返回天蓝「MR 已合并」徽章', () => {
    const merged = lifecycleEventBadgeProps('mr_merged');
    expect(merged).not.toBeNull();
    expect(merged!.label).toBe('MR 已合并');
    expect(merged!.className).toContain('bg-sky-50');
    expect(merged!.className).toContain('text-sky-700');
    expect(merged!.title).toContain('resolved');
  });

  it('null / undefined / 未知值都返回 null，让调用方走 review_mode 徽章', () => {
    expect(lifecycleEventBadgeProps(null)).toBeNull();
    expect(lifecycleEventBadgeProps(undefined)).toBeNull();
    expect(lifecycleEventBadgeProps('something-else')).toBeNull();
  });
});

// 问题与误报页 fp_status 徽章：确保四种状态视觉分明，未知走 fallback。
// 只测 pure function，不做组件渲染——contract 稳住即可。
describe('fpStatusBadgeProps', () => {
  it('NONE 返回 null，表示不渲染徽章', () => {
    expect(fpStatusBadgeProps('NONE')).toBeNull();
  });

  it('PENDING 返回琥珀色「误报待审」徽章', () => {
    const pending = fpStatusBadgeProps('PENDING');
    expect(pending).not.toBeNull();
    expect(pending?.label).toBe('误报待审');
    expect(pending?.className).toContain('bg-amber-50');
    expect(pending?.className).toContain('text-amber-700');
  });

  it('CONFIRMED 返回绿色「已确认误报」徽章', () => {
    const confirmed = fpStatusBadgeProps('CONFIRMED');
    expect(confirmed).not.toBeNull();
    expect(confirmed?.label).toBe('已确认误报');
    expect(confirmed?.className).toContain('bg-emerald-50');
    expect(confirmed?.className).toContain('text-emerald-700');
  });

  it('REJECTED 返回玫红色「误报驳回」徽章', () => {
    const rejected = fpStatusBadgeProps('REJECTED');
    expect(rejected).not.toBeNull();
    expect(rejected?.label).toBe('误报驳回');
    expect(rejected?.className).toContain('bg-rose-50');
    expect(rejected?.className).toContain('text-rose-700');
  });

  it('未知状态走中性灰兜底并保留原字符串，防止后端偷偷加新状态', () => {
    const unknown = fpStatusBadgeProps('SOMETHING_NEW');
    expect(unknown).not.toBeNull();
    expect(unknown?.label).toBe('SOMETHING_NEW');
    expect(unknown?.className).toContain('bg-zinc-50');
  });
});

// finding.status 徽章：新增 mr_closed 状态 + 已存在的 resolved。open/空不渲染。
describe('statusBadgeProps', () => {
  it('open / null / undefined 均返回 null，不渲染徽章', () => {
    expect(statusBadgeProps('open')).toBeNull();
    expect(statusBadgeProps(null)).toBeNull();
    expect(statusBadgeProps(undefined)).toBeNull();
  });

  it('resolved 走绿色「已修复」', () => {
    const resolved = statusBadgeProps('resolved');
    expect(resolved).not.toBeNull();
    expect(resolved?.label).toBe('已修复');
    expect(resolved?.className).toContain('bg-emerald-50');
  });

  it('mr_closed 走灰色「MR 已关闭」', () => {
    const closed = statusBadgeProps('mr_closed');
    expect(closed).not.toBeNull();
    expect(closed?.label).toBe('MR 已关闭');
    expect(closed?.className).toContain('bg-zinc-100');
    expect(closed?.className).toContain('text-zinc-600');
  });

  it('未知状态走中性灰兜底并保留原字符串', () => {
    const unknown = statusBadgeProps('SOMETHING_NEW');
    expect(unknown).not.toBeNull();
    expect(unknown?.label).toBe('SOMETHING_NEW');
    expect(unknown?.className).toContain('bg-zinc-50');
  });
});

// PR-B：从问题列表点"标记误报"应该唤起 MarkFalsePositiveDialog；从误报队列点
// "确认误报"应该唤起 ReviewFalsePositiveDialog（action=confirm）。做集成级 smoke。
describe('PR-B 误报处理弹窗集成', () => {
  function mountAppMock(finding: Record<string, unknown>, pendingFinding: Record<string, unknown>): void {
    mockFetch(async (url) => {
      if (url === '/health') {
        return jsonResponse({ status: 'ok', version: '0.1.0-dev', db: 'ok' });
      }
      if (url === '/api/auth/login') {
        return jsonResponse({ access_token: 'admin-token', token_type: 'bearer', expires_in: 86400, username: 'admin' });
      }
      if (url === '/api/engines') return jsonResponse([]);
      if (url === '/api/reviews/recent') return jsonResponse([]);
      if (url.startsWith('/api/findings')) {
        return jsonResponse({ items: [finding], total: 1, limit: 50, offset: 0 });
      }
      if (url.startsWith('/api/false-positives/pending')) {
        return jsonResponse({ items: [pendingFinding], total: 1, limit: 50, offset: 0 });
      }
      if (url.startsWith('/api/negative-examples')) {
        return jsonResponse({ items: [], total: 0, limit: 50, offset: 0 });
      }
      return jsonResponse({ detail: 'not found' }, false, 404);
    });
  }

  it('点击「标记误报」按钮打开 MarkFalsePositiveDialog', async () => {
    mountAppMock(
      {
        id: 'f-1', review_id: 'r-1', rule_id: 'no-print',
        title: 'print 未清理', severity: 'WARNING',
        file_path: 'src/foo.py', line_number: 3,
        fp_status: 'NONE', status: 'open',
      },
      {
        id: 'f-x', review_id: 'r-x', rule_id: 'no-eval',
        title: 'x', severity: 'WARNING',
        file_path: 'x.py', line_number: 1,
        fp_status: 'PENDING', status: 'open',
      },
    );
    render(<MemoryRouter><App /></MemoryRouter>);
    await loginAsAdmin();
    await userEvent.click(screen.getByRole('button', { name: '问题与误报' }));
    const markBtn = await screen.findByRole('button', { name: '标记误报' });
    await userEvent.click(markBtn);
    // 弹窗打开：出现标题 + 标记人默认值 admin。
    expect(await screen.findByText('标记为误报')).toBeInTheDocument();
    expect(screen.getByLabelText('标记人')).toHaveValue('admin');
  });

  it('点击「确认误报」按钮打开 ReviewFalsePositiveDialog（action=confirm）', async () => {
    mountAppMock(
      {
        id: 'f-1', review_id: 'r-1', rule_id: 'no-print',
        title: 't', severity: 'WARNING',
        file_path: 'a.py', line_number: 1,
        fp_status: 'NONE', status: 'open',
      },
      {
        id: 'f-p', review_id: 'r-p', rule_id: 'no-eval',
        title: 'eval 存在风险', severity: 'BLOCKER',
        file_path: 'src/bar.py', line_number: 9,
        fp_status: 'PENDING', status: 'open',
        fp_marked_by: 'bob', fp_marked_reason: '走白名单',
        fp_marked_at: '2026-07-19T00:00:00Z',
      },
    );
    render(<MemoryRouter><App /></MemoryRouter>);
    await loginAsAdmin();
    await userEvent.click(screen.getByRole('button', { name: /误报队列/ }));
    const confirmBtn = await screen.findByRole('button', { name: '确认误报' });
    await userEvent.click(confirmBtn);
    expect(await screen.findByText(/确认误报（将沉淀为负例）/)).toBeInTheDocument();
    // 弹窗内出现二次确认按钮："确认误报"（作为 primary label）。
    const dialogButtons = screen.getAllByRole('button', { name: '确认误报' });
    // 一个是列表里那颗，一个是弹窗 primary；数量应该 >= 2。
    expect(dialogButtons.length).toBeGreaterThanOrEqual(2);
  });
});
