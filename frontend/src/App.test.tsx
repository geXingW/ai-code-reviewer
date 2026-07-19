import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

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
        return jsonResponse({ status: 'ok', version: '0.1.0-dev', db: 'ok', redis: 'ok' });
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

    render(<App />);

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

  it('展示健康状态、引擎状态，并通过内部 Token 拉取最近审查记录', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    mockFetch(async (url, init) => {
      calls.push({ url, init });
      if (url === '/health') {
        return jsonResponse({ status: 'ok', version: '0.1.0-dev', db: 'ok', redis: 'error' });
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
        const headers = init?.headers as Record<string, string> | undefined;
        if (headers?.['X-Internal-Token'] !== 'test-internal-token') {
          return jsonResponse({ detail: 'Invalid internal token' }, false, 401);
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
      return jsonResponse({ detail: 'not found' }, false, 404);
    });

    render(<App />);

    expect(await screen.findByText('AI Code Reviewer')).toBeInTheDocument();
    await loginAsAdmin();
    expect(await screen.findByText('服务正常')).toBeInTheDocument();
    expect(screen.getByText('Redis 异常')).toBeInTheDocument();
    expect(screen.getByText('llm-direct')).toBeInTheDocument();
    expect(screen.getByText('暂无审查记录')).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('内部调用 Token'), 'test-internal-token');
    await userEvent.click(screen.getByRole('button', { name: '刷新最近审查' }));

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
    const recentCall = calls.find((call) => call.url === '/api/reviews/recent');
    expect(recentCall?.init?.headers).toEqual({ 'X-Internal-Token': 'test-internal-token' });
  });

  it('可通过表单手动触发一次 MR 审查', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    mockFetch(async (url, init) => {
      calls.push({ url, init });
      if (url === '/health') {
        return jsonResponse({ status: 'ok', version: '0.1.0-dev', db: 'ok', redis: 'ok' });
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
      if (url === '/api/reviews/recent') {
        return jsonResponse([]);
      }
      if (url === '/api/reviews') {
        return jsonResponse({
          review_id: '00000000-0000-0000-0000-000000000999',
          status: 'done',
          has_blocker: false,
          finding_count: 0,
          blocker_count: 0,
          policy_applied: 'master -> BLOCKER',
          review_url: '/api/reviews/00000000-0000-0000-0000-000000000999',
        });
      }
      return jsonResponse({ detail: 'not found' }, false, 404);
    });

    render(<App />);

    await loginAsAdmin();
    await userEvent.type(await screen.findByLabelText('内部调用 Token'), 'test-internal-token');
    await userEvent.type(screen.getByLabelText('GitLab 项目 ID'), '123');
    await userEvent.type(screen.getByLabelText('MR IID'), '7');
    await userEvent.type(screen.getByLabelText('目标分支'), 'master');
    await userEvent.type(screen.getByLabelText('源分支'), 'feature/demo');
    await userEvent.type(screen.getByLabelText('Commit SHA'), 'abc123');
    await userEvent.click(screen.getByRole('button', { name: '触发审查' }));

    await waitFor(() => expect(screen.getByText('审查完成，未发现阻断问题。')).toBeInTheDocument());
    const reviewCall = calls.find((call) => call.url === '/api/reviews');
    expect(reviewCall?.init?.headers).toEqual({ 'Content-Type': 'application/json', 'X-Internal-Token': 'test-internal-token' });
    expect(JSON.parse(String(reviewCall?.init?.body))).toMatchObject({
      project_id: 123,
      mr_iid: 7,
      target_branch: 'master',
      source_branch: 'feature/demo',
      commit_sha: 'abc123',
    });
  });

  it('可展开项目卡片、拖拽排序阻断策略并保存', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const projectId = '00000000-0000-0000-0000-000000000001';
    const project = {
      id: projectId,
      name: 'demo-project',
      gitlab_project_id: '123',
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
        return jsonResponse({ status: 'ok', version: '0.1.0-dev', db: 'ok', redis: 'ok' });
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
      if (url === '/api/rules') {
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

    const { container } = render(<App />);
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
});

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
