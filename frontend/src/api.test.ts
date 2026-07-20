import { afterEach, describe, expect, it, vi } from 'vitest';

import { createRule, deleteProject, fetchRules, setStoredAdminAccessToken, updateProvider, updateProject } from './api';

type Captured = { url?: string; init?: RequestInit };

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

/** 用捕获型 fetch 替换全局 fetch，返回固定的成功响应并记录请求参数。 */
function mockFetchAndCapture(): Captured {
  const captured: Captured = {};
  globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    captured.url = typeof input === 'string' ? input : input.toString();
    captured.init = init;
    const response = {
      ok: true,
      status: 200,
      json: async () => ({ id: '00000000-0000-0000-0000-000000000001' }),
    } as unknown as Response;
    return Promise.resolve(response);
  });
  return captured;
}

describe('API 客户端 payload 处理', () => {
  it('createRule 留空 rule_id 时 body 不包含该字段', async () => {
    setStoredAdminAccessToken('admin-token');
    const captured = mockFetchAndCapture();

    await createRule({
      title: 'No Print',
      prompt_snippet: 'avoid print',
      severity_default: 'WARNING',
      enabled: true,
    });

    expect(captured.url).toBe('/api/rules');
    expect(captured.init?.method).toBe('POST');
    const body = JSON.parse(String(captured.init?.body));
    expect(body).not.toHaveProperty('rule_id');
    expect(body.title).toBe('No Print');
  });

  it('createRule 显式传 rule_id 时 body 带上该字段', async () => {
    setStoredAdminAccessToken('admin-token');
    const captured = mockFetchAndCapture();

    await createRule({
      rule_id: 'no-print',
      title: 'No Print',
      prompt_snippet: 'avoid print',
      severity_default: 'WARNING',
      enabled: true,
    });

    const body = JSON.parse(String(captured.init?.body));
    expect(body.rule_id).toBe('no-print');
  });

  it('updateProvider 发 PATCH 到 /api/providers/:id 并注入鉴权头', async () => {
    setStoredAdminAccessToken('admin-token');
    const captured = mockFetchAndCapture();

    await updateProvider('p-1', { name: 'renamed', api_key: 'new-key' });

    expect(captured.url).toBe('/api/providers/p-1');
    expect(captured.init?.method).toBe('PATCH');
    const headers = captured.init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer admin-token');
    expect(headers['Content-Type']).toBe('application/json');
    const body = JSON.parse(String(captured.init?.body));
    expect(body).toEqual({ name: 'renamed', api_key: 'new-key' });
  });

  it('updateProject 携带 gitlab_access_token / webhook_secret 时随 body 发送', async () => {
    setStoredAdminAccessToken('admin-token');
    const captured = mockFetchAndCapture();

    await updateProject('proj-1', {
      name: 'renamed',
      gitlab_access_token: 'new-token',
      webhook_secret: 'new-secret',
    });

    expect(captured.url).toBe('/api/projects/proj-1');
    expect(captured.init?.method).toBe('PATCH');
    const body = JSON.parse(String(captured.init?.body));
    expect(body.name).toBe('renamed');
    expect(body.gitlab_access_token).toBe('new-token');
    expect(body.webhook_secret).toBe('new-secret');
  });

  it('deleteProject 发 DELETE 到 /api/projects/:id 并注入鉴权头', async () => {
    setStoredAdminAccessToken('admin-token');
    const captured = mockFetchAndCapture();

    await deleteProject('proj-42');

    expect(captured.url).toBe('/api/projects/proj-42');
    expect(captured.init?.method).toBe('DELETE');
    const headers = captured.init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer admin-token');
  });

  it('fetchRules 分页拉取：第一页满 100 条时继续翻页直到累计 total', async () => {
    setStoredAdminAccessToken('admin-token');
    const urls: string[] = [];
    const firstPage = Array.from({ length: 100 }, (_, i) => ({ id: `r${i}` }));
    const secondPage = Array.from({ length: 80 }, (_, i) => ({ id: `r${100 + i}` }));
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      urls.push(url);
      const isFirst = url.includes('offset=0');
      const body = { items: isFirst ? firstPage : secondPage, total: 180, limit: 100, offset: isFirst ? 0 : 100 };
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => body,
      } as unknown as Response);
    });

    const page = await fetchRules();
    expect(page.items).toHaveLength(180);
    expect(page.total).toBe(180);
    expect(urls).toEqual([
      '/api/rules?limit=100&offset=0',
      '/api/rules?limit=100&offset=100',
    ]);
  });
});
