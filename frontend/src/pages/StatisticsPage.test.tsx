/**
 * StatisticsPage 单测：只 mock 全局 fetch，走真实组件渲染路径。
 */

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setStoredAdminAccessToken } from '../api';
import { StatisticsPage } from './StatisticsPage';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

type MockResponse = {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
};

function jsonResponse(body: unknown): MockResponse {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

/**
 * 挂一个可分发的 fetch，按 URL 前缀分派。records 记录调用 URL 列表，
 * 方便后续断言"切换时间窗口后 5 个 stats 接口都被重新调用"。
 */
function installStatsFetch(options: {
  overview?: unknown;
  rules?: unknown;
  projects?: unknown;
  categories?: unknown;
  timeseries?: unknown;
}): { calls: string[] } {
  const calls: string[] = [];
  globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    calls.push(url);
    if (url.startsWith('/api/stats/overview')) {
      return Promise.resolve(jsonResponse(options.overview ?? DEFAULT_OVERVIEW) as unknown as Response);
    }
    if (url.startsWith('/api/stats/rules')) {
      return Promise.resolve(jsonResponse(options.rules ?? []) as unknown as Response);
    }
    if (url.startsWith('/api/stats/projects')) {
      return Promise.resolve(jsonResponse(options.projects ?? []) as unknown as Response);
    }
    if (url.startsWith('/api/stats/categories')) {
      return Promise.resolve(jsonResponse(options.categories ?? []) as unknown as Response);
    }
    if (url.startsWith('/api/stats/timeseries')) {
      return Promise.resolve(jsonResponse(options.timeseries ?? []) as unknown as Response);
    }
    return Promise.resolve(jsonResponse({}) as unknown as Response);
  });
  return { calls };
}

const DEFAULT_OVERVIEW = {
  days: 30,
  since: '2026-06-20T00:00:00+00:00',
  total_reviews: 42,
  total_findings: 128,
  total_blockers: 7,
  total_resolved: 15,
  avg_duration_ms: 3450,
  active_projects: 5,
  fp_pending: 2,
  fp_confirmed: 4,
  fp_rejected: 3,
  engine_usage: [{ engine: 'llm-direct', count: 40 }],
  provider_usage: [{ provider: 'ark', count: 40 }],
  status_breakdown: [{ status: 'done', count: 42 }],
};

describe('StatisticsPage', () => {
  it('拉数据后渲染 4 张 KPI 卡片，数字带上后端聚合结果', async () => {
    setStoredAdminAccessToken('admin-token');
    installStatsFetch({});

    render(<StatisticsPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('kpi-card')).toHaveLength(4);
    });
    // 总审查数
    expect(screen.getByText('42')).toBeInTheDocument();
    // 总问题数
    expect(screen.getByText('128')).toBeInTheDocument();
    // 平均耗时（3450ms → 3.45s）
    expect(screen.getByText('3.45s')).toBeInTheDocument();
    // 活跃项目
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('切换 30 → 7 天时 5 个 stats 接口都带上 days=7 重新调用', async () => {
    setStoredAdminAccessToken('admin-token');
    const { calls } = installStatsFetch({});
    const user = userEvent.setup();

    render(<StatisticsPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('kpi-card')).toHaveLength(4);
    });
    // 30 天默认调用一次，5 个 endpoint 都带 days=30。
    expect(calls.filter((u) => u.includes('days=30')).length).toBeGreaterThanOrEqual(5);
    const before = calls.length;

    await user.click(screen.getByRole('button', { name: '最近 7 天' }));

    await waitFor(() => {
      // 切换后 5 个 endpoint 都带上 days=7 重新调用一次。
      const sevenDayCalls = calls.filter((u) => u.includes('days=7'));
      expect(sevenDayCalls.length).toBeGreaterThanOrEqual(5);
    });
    // 至少多了 5 条请求。
    expect(calls.length - before).toBeGreaterThanOrEqual(5);
  });

  it('时间趋势：缺失日期的 review_count 渲染为 0', async () => {
    setStoredAdminAccessToken('admin-token');
    installStatsFetch({
      timeseries: [
        { date: '2026-06-20', review_count: 0, finding_count: 0, blocker_count: 0 },
        { date: '2026-06-21', review_count: 3, finding_count: 5, blocker_count: 1 },
        { date: '2026-06-22', review_count: 0, finding_count: 0, blocker_count: 0 },
      ],
    });

    render(<StatisticsPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('timeseries-bar')).toHaveLength(3);
    });
    const bars = screen.getAllByTestId('timeseries-bar');
    expect(bars[0].getAttribute('data-review-count')).toBe('0');
    expect(bars[1].getAttribute('data-review-count')).toBe('3');
    expect(bars[2].getAttribute('data-review-count')).toBe('0');
  });

  it('规则命中榜：按后端顺序渲染（finding_count 降序）', async () => {
    setStoredAdminAccessToken('admin-token');
    installStatsFetch({
      rules: [
        {
          rule_id: 'top-rule',
          title: 'Top',
          severity_default: 'WARNING',
          category_default: 'bug',
          finding_count: 50,
          blocker_count: 5,
          projects_hit: 3,
          fp_confirmed: 20,
          fp_rejected: 2,
          fp_pending: 1,
          fp_rate: 0.4,
          resolved_count: 3,
        },
        {
          rule_id: 'mid-rule',
          title: 'Middle',
          severity_default: 'INFO',
          category_default: 'style',
          finding_count: 10,
          blocker_count: 0,
          projects_hit: 2,
          fp_confirmed: 1,
          fp_rejected: 0,
          fp_pending: 0,
          fp_rate: 0.1,
          resolved_count: 0,
        },
        {
          rule_id: 'ghost',
          title: null,
          severity_default: null,
          category_default: null,
          finding_count: 1,
          blocker_count: 0,
          projects_hit: 1,
          fp_confirmed: 0,
          fp_rejected: 0,
          fp_pending: 0,
          fp_rate: 0.0,
          resolved_count: 0,
        },
      ],
    });

    render(<StatisticsPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('rule-row')).toHaveLength(3);
    });
    const rows = screen.getAllByTestId('rule-row');
    expect(rows[0].getAttribute('data-rule-id')).toBe('top-rule');
    expect(rows[1].getAttribute('data-rule-id')).toBe('mid-rule');
    expect(rows[2].getAttribute('data-rule-id')).toBe('ghost');
    // 被删规则显示占位文案。
    expect(within(rows[2]).getByText('（规则已删除）')).toBeInTheDocument();
  });

  it('空态：全部接口返回空数据时展示 empty message', async () => {
    setStoredAdminAccessToken('admin-token');
    installStatsFetch({
      overview: {
        ...DEFAULT_OVERVIEW,
        total_reviews: 0,
        total_findings: 0,
        total_blockers: 0,
        total_resolved: 0,
        avg_duration_ms: null,
        active_projects: 0,
        fp_pending: 0,
        fp_confirmed: 0,
        fp_rejected: 0,
        engine_usage: [],
        provider_usage: [],
        status_breakdown: [],
      },
      rules: [],
      projects: [],
      categories: [],
      timeseries: [],
    });

    render(<StatisticsPage />);

    await waitFor(() => {
      expect(screen.getByTestId('stats-empty')).toBeInTheDocument();
    });
    expect(screen.getByTestId('stats-empty').textContent).toContain('暂无审查数据');
  });
});
