import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { Page } from '../api';
import { toErrorMessage } from '../lib/format';

/**
 * 服务端分页列表 hook：统一「页码 + 每页条数 + 排序 + 筛选 + 防抖搜索 + reload」
 * 的状态与请求编排，配合 antd Table 的 pagination / onChange 使用。
 *
 * - setFilters(patch)：合并筛选并回到第 1 页（搜索词、下拉筛选共用入口）；
 * - setSort(field, order)：接 antd Table 列 sorter，映射为后端 sort 字符串
 *   （`-` 前缀表示降序，与 FastAPI 后端 _ALLOWED_SORTS 约定一致）；
 * - pagination：直接透传给 <Table pagination={...}>，showTotal 显示服务端 total。
 */

export type PagedListFetcher<T, F> = (args: {
  limit: number;
  offset: number;
  sort?: string;
  filters: F;
}) => Promise<Page<T>>;

export function usePagedList<T, F extends Record<string, unknown>>(
  fetcher: PagedListFetcher<T, F>,
  initialFilters: F,
  options?: { pageSize?: number },
) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(options?.pageSize ?? 20);
  const [sort, setSortState] = useState<string | undefined>(undefined);
  const [filters, setFiltersState] = useState<F>(initialFilters);
  const [data, setData] = useState<Page<T> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // fetcher 通常由页面内联定义（闭包引用 options 下拉数据等），用 ref 避免
  // 把它放进 effect 依赖导致反复请求；竞态用请求序号兜底，只采纳最新一次。
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const requestSeqRef = useRef(0);

  useEffect(() => {
    const seq = ++requestSeqRef.current;
    let active = true;
    setLoading(true);
    setError(null);
    fetcherRef
      .current({
        limit: pageSize,
        offset: (page - 1) * pageSize,
        sort,
        filters,
      })
      .then((pageData) => {
        if (active && seq === requestSeqRef.current) {
          setData(pageData);
        }
      })
      .catch((caught: unknown) => {
        if (active && seq === requestSeqRef.current) {
          setError(toErrorMessage(caught));
        }
      })
      .finally(() => {
        if (active && seq === requestSeqRef.current) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [page, pageSize, sort, filters, reloadKey]);

  const setFilters = useCallback((patch: Partial<F>) => {
    setFiltersState((prev) => ({ ...prev, ...patch }));
    setPage(1);
  }, []);

  const setSort = useCallback((field: string | undefined, order: 'ascend' | 'descend' | null) => {
    setSortState(order && field ? (order === 'descend' ? `-${field}` : field) : undefined);
  }, []);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  const pagination = useMemo(
    () => ({
      current: page,
      pageSize,
      total: data?.total ?? 0,
      showSizeChanger: true,
      showTotal: (total: number) => `共 ${total} 条`,
      onChange: (next: number, nextSize: number) => {
        if (nextSize !== pageSize) {
          setPageSize(nextSize);
          setPage(1);
        } else {
          setPage(next);
        }
      },
    }),
    [page, pageSize, data?.total],
  );

  return {
    items: data?.items ?? [],
    total: data?.total ?? 0,
    loading,
    error,
    filters,
    sort,
    page,
    pageSize,
    setFilters,
    setSort,
    reload,
    pagination,
  };
}

/** 输入防抖：搜索框先走本地 state，防抖值再喂给 usePagedList.setFilters。 */
export function useDebouncedValue<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
