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
  type LucideIcon,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export type PageKey =
  | 'dashboard'
  | 'providers'
  | 'rules'
  | 'projects'
  | 'reviews'
  | 'findings'
  | 'falsePositives'
  | 'engines';

interface NavItem {
  key: PageKey;
  label: string;
  icon: LucideIcon;
}

interface AppShellProps {
  activePage: PageKey;
  onNavigate: (page: PageKey) => void;
  health: { status: string; version?: string } | null;
  onLogout: () => void;
  children: React.ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  { key: 'dashboard', label: '仪表盘', icon: LayoutDashboard },
  { key: 'providers', label: '模型供应商', icon: Boxes },
  { key: 'rules', label: '审查规则', icon: ListChecks },
  { key: 'projects', label: 'GitLab 项目', icon: FolderGit2 },
  { key: 'reviews', label: '审查记录', icon: ScrollText },
  { key: 'findings', label: '问题与误报', icon: AlertTriangle },
  { key: 'falsePositives', label: '误报队列', icon: Filter },
  { key: 'engines', label: '引擎配置', icon: Cpu },
];

/**
 * 现代管理台外壳：顶栏 56px + 侧栏 240px 的经典 SaaS 布局。
 * 外层挂 `data-ui="new"`，启用 globals.css 里的 Indigo / Inter tokens。
 * 业务面板作为 children 注入主内容区，自身样式不动。
 */
export function AppShell({ activePage, onNavigate, health, onLogout, children }: AppShellProps) {
  const healthy = health?.status === 'ok';
  const healthLabel = healthy
    ? health?.version
      ? `服务正常 · v${health.version}`
      : '服务正常'
    : '健康未知';

  return (
    <div
      data-ui="new"
      className="min-h-screen bg-background text-foreground font-sans flex flex-col"
    >
      <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b bg-card px-6">
        <div className="flex items-center gap-2">
          <ShieldCheck className="size-5 text-primary" />
          <span className="text-base font-bold">AI Code Reviewer</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <span
              className={cn(
                'size-2 rounded-full',
                healthy ? 'bg-primary' : 'bg-muted-foreground/40',
              )}
            />
            {healthLabel}
          </span>
          <Button variant="ghost" size="sm" className="bg-transparent" onClick={onLogout}>
            退出登录
          </Button>
        </div>
      </header>

      <div className="flex flex-1">
        <nav
          aria-label="管理页面导航"
          className="flex w-60 shrink-0 flex-col gap-1 border-r bg-card p-3"
        >
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = activePage === item.key;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => onNavigate(item.key)}
                className={cn(
                  'w-full flex items-center gap-3 rounded-md bg-transparent px-3 py-2 text-sm font-medium transition-colors',
                  active
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                )}
              >
                <Icon className="size-4 shrink-0" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <main className="flex-1 min-w-0 overflow-y-auto">
          <div className="mx-auto w-full max-w-screen-2xl px-8 py-6">{children}</div>
        </main>
      </div>
    </div>
  );
}
