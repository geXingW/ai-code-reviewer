import * as React from 'react';
import {
  AlertTriangle,
  Boxes,
  Cpu,
  Filter,
  FolderGit2,
  LayoutDashboard,
  ListChecks,
  ScrollText,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';

import { cn } from '@/lib/utils';

export type PageKey =
  | 'dashboard'
  | 'providers'
  | 'rules'
  | 'projects'
  | 'reviews'
  | 'findings'
  | 'falsePositives'
  | 'negativeExamples'
  | 'engines';

interface NavItem {
  key: PageKey;
  label: string;
  icon: LucideIcon;
  badge?: string;
}

interface NavSection {
  label: string;
  items: NavItem[];
}

interface AppShellProps {
  activePage: PageKey;
  onNavigate: (page: PageKey) => void;
  health: { status: string; version?: string } | null;
  onLogout: () => void;
  children: React.ReactNode;
}

/**
 * 侧栏导航分组：工作台 4 项 + 配置 4 项（顺序即侧栏展示顺序）。
 * 类型联合 PageKey 保持不变，仅数组顺序按分组重排。
 */
const NAV_SECTIONS: NavSection[] = [
  {
    label: '工作台',
    items: [
      { key: 'dashboard', label: '仪表盘', icon: LayoutDashboard },
      { key: 'reviews', label: '审查记录', icon: ScrollText },
      { key: 'falsePositives', label: '误报队列', icon: Filter, badge: '3' },
      { key: 'negativeExamples', label: '负样本库', icon: Sparkles },
      { key: 'findings', label: '问题与误报', icon: AlertTriangle },
    ],
  },
  {
    label: '配置',
    items: [
      { key: 'providers', label: '模型供应商', icon: Boxes },
      { key: 'rules', label: '审查规则', icon: ListChecks },
      { key: 'projects', label: 'GitLab 项目', icon: FolderGit2 },
      { key: 'engines', label: '引擎配置', icon: Cpu },
    ],
  },
];

/**
 * Linear 风格管理台外壳：侧栏 224px（w-56）+ 顶栏 44px（h-11）。
 * 主 CTA 黑、Indigo 仅作激活态点缀；边框极淡、无阴影、靠留白分层。
 * 业务面板作为 children 注入主内容滚动区，自身样式不动。
 */
export function AppShell({ activePage, onNavigate, health, onLogout, children }: AppShellProps) {
  const healthy = health?.status === 'ok';
  const statusDotClass = !health
    ? 'bg-zinc-300'
    : healthy
      ? 'bg-emerald-500'
      : 'bg-rose-500';
  const versionLabel = health?.version ? ` · v${health.version}` : '';
  const currentLabel =
    NAV_SECTIONS.flatMap((section) => section.items).find((item) => item.key === activePage)
      ?.label ?? '';

  function handleUserClick() {
    if (typeof window !== 'undefined' && window.confirm('确定退出登录？')) {
      onLogout();
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#FAFAFA] font-sans text-foreground">
      {/* ─────────────── 侧栏 ─────────────── */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-zinc-200 bg-white">
        {/* Workspace header */}
        <div className="flex h-11 items-center gap-2 border-b border-zinc-200 px-3">
          <div className="flex size-6 items-center justify-center rounded-md bg-linear-to-br from-indigo-500 to-indigo-700">
            <ShieldCheck size={14} strokeWidth={2.5} className="text-white" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-semibold leading-tight text-zinc-900">
              AI Code Reviewer
            </div>
            <div className="flex items-center gap-1.5 leading-tight">
              <span className={cn('size-1.5 shrink-0 rounded-full', statusDotClass)} />
              <span className="truncate text-[11px] text-zinc-500">production{versionLabel}</span>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav aria-label="管理页面导航" className="flex-1 space-y-4 overflow-y-auto p-3">
          {NAV_SECTIONS.map((section) => (
            <div key={section.label}>
              <div className="mb-2 px-2 text-[11px] font-medium uppercase tracking-[0.06em] text-zinc-400">
                {section.label}
              </div>
              <div className="space-y-0.5">
                {section.items.map((item) => {
                  const Icon = item.icon;
                  const active = activePage === item.key;
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() => onNavigate(item.key)}
                      className={cn(
                        'flex h-7 w-full items-center gap-2 rounded-md px-2 text-[13px] transition-colors',
                        active
                          ? 'bg-black/[0.06] font-medium text-zinc-900'
                          : 'text-zinc-600 hover:bg-black/5 hover:text-zinc-900',
                      )}
                    >
                      <Icon
                        size={14}
                        strokeWidth={1.75}
                        className={cn('shrink-0', active ? 'text-[#4F46E5] opacity-100' : 'opacity-70')}
                      />
                      <span>{item.label}</span>
                      {item.badge ? (
                        <span className="ml-auto inline-flex h-4 items-center rounded border border-zinc-200 bg-zinc-100 px-1.5 text-[10px] font-medium text-zinc-600">
                          {item.badge}
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* User footer */}
        <div className="border-t border-zinc-200 p-2">
          <button
            type="button"
            onClick={handleUserClick}
            className="flex w-full items-center gap-2 rounded-md p-1.5 text-left transition-colors hover:bg-zinc-50"
          >
            <div className="flex size-6 shrink-0 items-center justify-center rounded-full bg-indigo-500 text-[11px] font-medium text-white">
              A
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[12px] font-medium leading-tight text-zinc-900">admin</div>
              <div className="truncate text-[11px] leading-tight text-zinc-500">Bearer Token</div>
            </div>
            <MoreHorizontalIcon className="size-3.5 shrink-0 text-zinc-400" />
          </button>
        </div>
      </aside>

      {/* ─────────────── 主区 ─────────────── */}
      <main className="flex min-w-0 flex-1 flex-col">
        {/* Topbar */}
        <header className="flex h-11 items-center gap-3 border-b border-zinc-200 bg-white px-4">
          <div className="flex items-center gap-1.5 text-[13px]">
            <span className="text-zinc-500">工作台</span>
            <ChevronRightIcon className="size-3.5 text-zinc-300" />
            <span className="font-medium text-zinc-900">{currentLabel}</span>
          </div>

          <div className="flex-1" />

          {/* Search（dummy，暂不实现搜索） */}
          <div className="relative">
            <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-zinc-400" />
            <input
              type="text"
              aria-label="搜索"
              placeholder="搜索…"
              className="h-8 w-56 rounded-md border border-[#E4E4E7] bg-white pl-8 pr-12 text-[13px] text-foreground placeholder:text-zinc-400 hover:border-[#D4D4D8] focus-visible:border-[#6366F1] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <kbd className="absolute right-2 top-1/2 -translate-y-1/2 rounded border border-b-2 border-[#E4E4E7] bg-[#F4F4F5] px-1.5 py-0.5 font-mono text-[11px] text-zinc-500">
              ⌘K
            </kbd>
          </div>

          {/* Bell */}
          <button
            type="button"
            aria-label="通知"
            className="inline-flex size-[26px] items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
          >
            <BellIcon className="size-3.5" />
          </button>
        </header>

        {/* Content scroll area */}
        <div className="flex-1 overflow-y-auto">
          <div className="p-6">{children}</div>
        </div>
      </main>
    </div>
  );
}

/* ─────────────── 顶栏 / 用户区 inline 图标（与 mockup 一致，避免新增 lucide 依赖） ─────────────── */

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} className={className}>
      <path
        d="M21 21l-4.35-4.35M17 10a7 7 0 11-14 0 7 7 0 0114 0z"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function BellIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} className={className}>
      <path
        d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ChevronRightIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} className={className}>
      <path d="M9 5l7 7-7 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function MoreHorizontalIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} className={className}>
      <path d="M12 6v.01M12 12v.01M12 18v.01" strokeLinecap="round" />
    </svg>
  );
}
