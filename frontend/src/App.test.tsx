import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import App from './App';

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
});
