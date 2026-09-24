/**
 * 全局搜索命令面板（顶栏搜索框 / ⌘K 触发）。
 *
 * 顶栏搜索此前是纯装饰（input 无 onChange、⌘K 徽章无监听）。本组件把它做成
 * 真搜索：跨实体查询后端已有的服务端搜索参数——
 * - 项目 / 规则 / 供应商：`q` 模糊匹配
 * - 问题与误报：`file_path` ilike
 * - 审查记录：`mr_iid` 精确匹配（输入纯数字时才查询）
 * - 页面快速跳转：本地过滤导航项
 *
 * 点击结果跳转到对应页面并带入初始筛选条件（onNavigateWithFilters）。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  FileDoneOutlined,
  FilterOutlined,
  GitlabOutlined,
  MenuOutlined,
  SearchOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { Empty, Input, Modal, Spin, type InputRef } from 'antd';

import {
  fetchFindings,
  fetchProjects,
  fetchProviders,
  fetchReviewRecords,
  fetchRulesPage,
} from '../api';
import { useDebouncedValue } from '../hooks/usePagedList';
import type { PageKey } from './layout/AppShell';

export type SearchFilters = Record<string, string>;

interface GlobalSearchModalProps {
  open: boolean;
  onClose: () => void;
  onNavigate: (page: PageKey, filters?: SearchFilters) => void;
  /** 导航分组（工作台/配置/系统管理），用于页面跳转候选与图标。 */
  navItems: Array<{
    key: PageKey;
    label: string;
    icon: React.ComponentType<{ style?: React.CSSProperties }>;
  }>;
}

type ResultAction = { page: PageKey; filters?: SearchFilters };

interface ResultGroup {
  key: string;
  title: string;
  items: Array<{ key: string; title: string; description?: string; icon: React.ReactNode; action: ResultAction }>;
}

export function GlobalSearchModal({ open, onClose, onNavigate, navItems }: GlobalSearchModalProps) {
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [groups, setGroups] = useState<ResultGroup[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<InputRef | null>(null);
  const debouncedQuery = useDebouncedValue(query.trim(), 250);

  // 打开时清空上次的搜索词，聚焦输入框。
  useEffect(() => {
    if (open) {
      setQuery('');
      setGroups([]);
      setActiveIndex(0);
      window.setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // 页面跳转候选：本地过滤导航项（大小写不敏感）。
  const pageMatches = useMemo(() => {
    const q = debouncedQuery.toLowerCase();
    return navItems.filter(
      (item) => !q || item.label.toLowerCase().includes(q) || item.key.toLowerCase().includes(q),
    );
  }, [debouncedQuery, navItems]);

  useEffect(() => {
    const q = debouncedQuery;
    if (!open || !q) {
      setGroups([]);
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);

    const queries: Array<Promise<void>> = [];
    const nextGroups: ResultGroup[] = [];

    queries.push(
      fetchProjects({ q, limit: 5 })
        .then((page) => {
          if (!active || page.items.length === 0) return;
          nextGroups.push({
            key: 'projects',
            title: 'GitLab 项目',
            items: page.items.map((project) => ({
              key: `project-${project.id}`,
              title: project.name,
              description: `GitLab ${project.gitlab_project_id}`,
              icon: <GitlabOutlined />,
              action: { page: 'projects', filters: { q: project.name } },
            })),
          });
        })
        .catch(() => {}),
    );
    queries.push(
      fetchRulesPage({ q, limit: 5 })
        .then((page) => {
          if (!active || page.items.length === 0) return;
          nextGroups.push({
            key: 'rules',
            title: '审查规则',
            items: page.items.map((rule) => ({
              key: `rule-${rule.id}`,
              title: rule.rule_id,
              description: rule.title,
              icon: <MenuOutlined />,
              action: { page: 'rules', filters: { q: rule.rule_id } },
            })),
          });
        })
        .catch(() => {}),
    );
    queries.push(
      fetchProviders({ q, limit: 5 })
        .then((page) => {
          if (!active || page.items.length === 0) return;
          nextGroups.push({
            key: 'providers',
            title: '模型供应商',
            items: page.items.map((provider) => ({
              key: `provider-${provider.id}`,
              title: provider.name,
              description: `${provider.protocol} · ${provider.model}`,
              icon: <FilterOutlined />,
              action: { page: 'providers', filters: { q: provider.name } },
            })),
          });
        })
        .catch(() => {}),
    );
    queries.push(
      fetchFindings({ file_path: q, limit: 5 })
        .then((page) => {
          if (!active || page.items.length === 0) return;
          nextGroups.push({
            key: 'findings',
            title: '问题与误报',
            items: page.items.map((finding) => ({
              key: `finding-${finding.id}`,
              title: finding.title,
              description: `${finding.file_path}:${finding.line_number ?? '-'} · ${finding.rule_id}`,
              icon: <WarningOutlined />,
              action: { page: 'findings', filters: { file_path: finding.file_path } },
            })),
          });
        })
        .catch(() => {}),
    );
    // 审查记录仅支持 mr_iid 精确匹配，输入纯数字才发起查询。
    if (/^\d+$/.test(q)) {
      queries.push(
        fetchReviewRecords({ mr_iid: q, limit: 5 })
          .then((page) => {
            if (!active || page.items.length === 0) return;
            nextGroups.push({
              key: 'reviews',
              title: '审查记录',
              items: page.items.map((record) => ({
                key: `review-${record.id}`,
                title: `MR !${record.mr_iid}`,
                description: `${record.project_name ?? ''} ${record.source_branch} → ${record.target_branch}`.trim(),
                icon: <FileDoneOutlined />,
                action: { page: 'reviews', filters: { mr_iid: String(record.mr_iid) } },
              })),
            });
          })
          .catch(() => {}),
      );
    }

    void Promise.all(queries).then(() => {
      if (active) {
        setGroups(nextGroups);
        setLoading(false);
        setActiveIndex(0);
      }
    });
    return () => {
      active = false;
    };
  }, [debouncedQuery, open]);

  const pageGroup: ResultGroup = useMemo(
    () => ({
      key: 'pages',
      title: '页面跳转',
      items: pageMatches.map((item) => ({
        key: `page-${item.key}`,
        title: item.label,
        icon: <item.icon />,
        action: { page: item.key },
      })),
    }),
    [pageMatches],
  );

  const allGroups = useMemo(
    () => [pageGroup, ...groups].filter((group) => group.items.length > 0),
    [pageGroup, groups],
  );
  const flatItems = useMemo(() => allGroups.flatMap((group) => group.items), [allGroups]);

  function handleNavigate(action: ResultAction) {
    onClose();
    onNavigate(action.page, action.filters);
  }

  function handleKeyDown(event: React.KeyboardEvent) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((prev) => Math.min(prev + 1, flatItems.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((prev) => Math.max(prev - 1, 0));
    } else if (event.key === 'Enter') {
      const item = flatItems[activeIndex];
      if (item) {
        handleNavigate(item.action);
      }
    }
  }

  let flatIndex = -1;

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      width={560}
      styles={{ body: { padding: 0 } }}
      closable={false}
    >
      <div className="border-b border-zinc-100 px-4 py-3">
        <Input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="搜索项目 / 规则 / 供应商 / 问题文件路径 / MR 号，或输入页面名跳转…"
          variant="borderless"
          size="large"
          prefix={<SearchOutlined style={{ color: '#A1A1AA' }} />}
          allowClear
        />
      </div>
      <div className="max-h-[420px] overflow-y-auto p-2">
        {loading ? (
          <div className="flex justify-center py-8">
            <Spin />
          </div>
        ) : allGroups.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={debouncedQuery ? '没有匹配的结果' : '输入关键字搜索，↑↓ 选择，回车跳转'}
            className="py-8"
          />
        ) : (
          allGroups.map((group) => (
            <div key={group.key} className="mb-1">
              <div className="px-2 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                {group.title}
              </div>
              {group.items.map((item) => {
                flatIndex += 1;
                const current = flatIndex;
                const isActive = current === activeIndex;
                return (
                  <button
                    key={item.key}
                    type="button"
                    onMouseEnter={() => setActiveIndex(current)}
                    onClick={() => handleNavigate(item.action)}
                    className={`flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors ${
                      isActive ? 'bg-indigo-50' : 'hover:bg-zinc-50'
                    }`}
                  >
                    <span className="flex size-6 shrink-0 items-center justify-center rounded bg-zinc-100 text-[12px] text-zinc-500">
                      {item.icon}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium text-zinc-900">
                        {item.title}
                      </span>
                      {item.description ? (
                        <span className="block truncate text-[11px] text-zinc-500">
                          {item.description}
                        </span>
                      ) : null}
                    </span>
                    {isActive ? (
                      <span className="shrink-0 rounded border border-zinc-200 bg-zinc-50 px-1 font-mono text-[10px] text-zinc-400">
                        ↵
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          ))
        )}
      </div>
    </Modal>
  );
}
