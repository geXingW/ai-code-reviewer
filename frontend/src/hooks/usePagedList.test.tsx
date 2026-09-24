/**
 * usePagedList hook 单测：服务端分页状态编排（页码 / 每页条数 / 排序 / 筛选
 * / reload / 竞态兜底）是全站列表页的核心逻辑，值得纯 hook 级回归。
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useDebouncedValue, usePagedList } from './usePagedList';
import type { Page } from '../api';

type Row = { id: string };
type Filters = { q: string };

function makePage(items: Row[], total: number): Page<Row> {
  return { items, total, limit: items.length, offset: 0 };
}

describe('usePagedList', () => {
  it('挂载时按默认 pageSize=20 拉第 1 页，并暴露 total / pagination', async () => {
    const fetcher = vi.fn().mockResolvedValue(makePage([{ id: 'a' }], 41));
    const { result } = renderHook(() => usePagedList<Row, Filters>(fetcher, { q: '' }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetcher).toHaveBeenCalledWith({ limit: 20, offset: 0, sort: undefined, filters: { q: '' } });
    expect(result.current.items).toEqual([{ id: 'a' }]);
    expect(result.current.total).toBe(41);
    expect(result.current.pagination).toMatchObject({ current: 1, pageSize: 20, total: 41 });
  });

  it('翻页换算 offset；setFilters 合并筛选并回到第 1 页', async () => {
    const fetcher = vi.fn().mockResolvedValue(makePage([], 100));
    const { result } = renderHook(() => usePagedList<Row, Filters>(fetcher, { q: '' }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => {
      result.current.pagination.onChange(3, 20);
    });
    await waitFor(() =>
      expect(fetcher).toHaveBeenLastCalledWith({ limit: 20, offset: 40, sort: undefined, filters: { q: '' } }),
    );

    act(() => {
      result.current.setFilters({ q: 'abc' });
    });
    await waitFor(() =>
      expect(fetcher).toHaveBeenLastCalledWith({ limit: 20, offset: 0, sort: undefined, filters: { q: 'abc' } }),
    );
    expect(result.current.pagination.current).toBe(1);
  });

  it('setSort 映射后端 sort 字符串（- 前缀为降序）', async () => {
    const fetcher = vi.fn().mockResolvedValue(makePage([], 0));
    const { result } = renderHook(() => usePagedList<Row, Filters>(fetcher, { q: '' }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => {
      result.current.setSort('created_at', 'descend');
    });
    await waitFor(() =>
      expect(fetcher).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: '-created_at' }),
      ),
    );

    act(() => {
      result.current.setSort('created_at', null);
    });
    await waitFor(() =>
      expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ sort: undefined })),
    );
  });

  it('只采纳最新一次请求的结果（竞态兜底）', async () => {
    let resolveFirst: (value: Page<Row>) => void = () => {};
    const first = new Promise<Page<Row>>((resolve) => {
      resolveFirst = resolve;
    });
    const fetcher = vi
      .fn()
      .mockImplementationOnce(() => first)
      .mockResolvedValue(makePage([{ id: 'latest' }], 1));

    const { result } = renderHook(() => usePagedList<Row, Filters>(fetcher, { q: '' }));
    // 第一个请求还在挂起时翻页触发第二个请求。
    act(() => {
      result.current.pagination.onChange(2, 20);
    });
    await waitFor(() => expect(result.current.items).toEqual([{ id: 'latest' }]));

    // 迟到的第一个请求不应覆盖新结果。
    act(() => {
      resolveFirst(makePage([{ id: 'stale' }], 1));
    });
    expect(result.current.items).toEqual([{ id: 'latest' }]);
  });

  it('请求失败时暴露 error 消息', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => usePagedList<Row, Filters>(fetcher, { q: '' }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('boom');
  });
});

describe('useDebouncedValue', () => {
  it('延迟同步输入值', async () => {
    const { result, rerender } = renderHook(({ value }: { value: string }) => useDebouncedValue(value, 20), {
      initialProps: { value: 'a' },
    });
    expect(result.current).toBe('a');
    rerender({ value: 'ab' });
    expect(result.current).toBe('a');
    await waitFor(() => expect(result.current).toBe('ab'), { timeout: 200 });
  });
});
