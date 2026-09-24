/**
 * 管理台外壳（antd 版）：Sider 导航 + Header 工具栏 + Content 滚动区。
 *
 * - 侧栏 Menu 保留三组导航与 RBAC `page:*` 权限过滤；
 * - 顶栏搜索为真搜索（⌘K / Ctrl+K 唤起 GlobalSearchModal，跨实体服务端查询）；
 * - 通知铃铛接真实数据：Badge 显示误报待审核总数，点击跳转误报队列；
 * - 用户区为 Dropdown，退出登录需二次确认。
 */

import * as React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BellOutlined,
  CloudServerOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FileDoneOutlined,
  FileTextOutlined,
  FilterOutlined,
  GitlabOutlined,
  LinkOutlined,
  LogoutOutlined,
  SearchOutlined,
  SafetyCertificateOutlined,
  SafetyOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { App as AntApp, Badge, Breadcrumb, Button, Dropdown, Layout, Menu } from 'antd';

import { fetchPendingFalsePositives } from '../../api';
import { GlobalSearchModal } from '../GlobalSearchModal';

export type PageKey =
  | 'dashboard'
  | 'providers'
  | 'global-prompt'
  | 'rules'
  | 'projects'
  | 'user-mappings'
  | 'reviews'
  | 'findings'
  | 'falsePositives'
  | 'negativeExamples'
  | 'engines'
  | 'users'
  | 'roles';

interface NavItem {
  key: PageKey;
  label: string;
  icon: React.ComponentType<{ style?: React.CSSProperties }>;
}

interface NavSection {
  label: string;
  items: NavItem[];
}

/** 导航分组：工作台 5 项 + 配置 6 项 + 系统管理 2 项（顺序即侧栏展示顺序）。 */
export const NAV_SECTIONS: NavSection[] = [
  {
    label: '工作台',
    items: [
      { key: 'dashboard', label: '仪表盘', icon: AppstoreOutlined },
      { key: 'reviews', label: '审查记录', icon: FileDoneOutlined },
      { key: 'falsePositives', label: '误报队列', icon: FilterOutlined },
      { key: 'negativeExamples', label: '负样本库', icon: ExperimentOutlined },
      { key: 'findings', label: '问题与误报', icon: AlertOutlined },
    ],
  },
  {
    label: '配置',
    items: [
      { key: 'providers', label: '模型供应商', icon: CloudServerOutlined },
      { key: 'global-prompt', label: '全局提示词', icon: FileTextOutlined },
      { key: 'rules', label: '审查规则', icon: AuditOutlined },
      { key: 'projects', label: 'GitLab 项目', icon: GitlabOutlined },
      { key: 'user-mappings', label: '用户映射', icon: LinkOutlined },
      { key: 'engines', label: '引擎配置', icon: DeploymentUnitOutlined },
    ],
  },
  {
    label: '系统管理',
    items: [
      { key: 'users', label: '用户管理', icon: UserOutlined },
      { key: 'roles', label: '角色管理', icon: SafetyCertificateOutlined },
    ],
  },
];

export const ALL_NAV_ITEMS: Array<NavItem> = NAV_SECTIONS.flatMap((section) => section.items);

interface AppShellProps {
  activePage: PageKey;
  onNavigate: (page: PageKey) => void;
  /** 带初始筛选条件跳转（⌘K 搜索结果落地）；未传时退化为普通跳转。 */
  onNavigateWithFilters?: (page: PageKey, filters: Record<string, string>) => void;
  health: { status: string; version?: string } | null;
  onLogout: () => void;
  children: React.ReactNode;
  // PR-2 RBAC：当前登录用户（用于用户区展示）与权限列表（用于过滤导航菜单）。
  currentUser?: { username: string; display_name: string | null };
  permissions?: string[];
}

const isMac =
  typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform ?? navigator.userAgent);

export function AppShell({
  activePage,
  onNavigate,
  onNavigateWithFilters,
  health,
  onLogout,
  children,
  currentUser,
  permissions,
}: AppShellProps) {
  const { modal } = AntApp.useApp();
  const [searchOpen, setSearchOpen] = useState(false);
  // 通知角标：误报待审核总数。切页时刷新一次，处理后回到队列页数字即更新。
  const [pendingFpCount, setPendingFpCount] = useState<number | null>(null);

  const healthy = health?.status === 'ok';
  const versionLabel = health?.version ? ` · v${health.version}` : '';
  const currentLabel = ALL_NAV_ITEMS.find((item) => item.key === activePage)?.label ?? '';

  // PR-2 RBAC：按权限过滤导航项。permissions 为空（未传入）时显示全部菜单。
  const hasPermission = useCallback(
    (pageKey: PageKey): boolean => {
      if (!permissions || permissions.length === 0) return true;
      return permissions.includes(`page:${pageKey}`);
    },
    [permissions],
  );

  useEffect(() => {
    let active = true;
    fetchPendingFalsePositives({ limit: 1 })
      .then((page) => {
        if (active) {
          setPendingFpCount(page.total);
        }
      })
      .catch(() => {
        if (active) {
          setPendingFpCount(null);
        }
      });
    return () => {
      active = false;
    };
  }, [activePage]);

  // ⌘K / Ctrl+K 唤起全局搜索。
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearchOpen((prev) => !prev);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const menuItems = useMemo(() => {
    return NAV_SECTIONS.map((section) => {
      const visibleItems = section.items.filter((item) => hasPermission(item.key));
      return {
        key: section.label,
        label: section.label,
        type: 'group' as const,
        children: visibleItems.map((item) => ({
          key: item.key,
          icon: <item.icon />,
          label: item.label,
        })),
      };
    }).filter((section) => section.children.length > 0);
  }, [hasPermission]);

  function handleUserMenuClick({ key }: { key: string }) {
    if (key === 'logout') {
      modal.confirm({
        title: '确定退出登录？',
        content: '退出后需要重新输入用户名和密码。',
        okText: '退出',
        cancelText: '取消',
        onOk: onLogout,
      });
    }
  }

  function handleSearchNavigate(page: PageKey, filters?: Record<string, string>) {
    if (filters && Object.keys(filters).length > 0 && onNavigateWithFilters) {
      onNavigateWithFilters(page, filters);
    } else {
      onNavigate(page);
    }
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider
        width={224}
        theme="light"
        style={{ borderRight: '1px solid #E4E4E7', display: 'flex', flexDirection: 'column' }}
      >
        <div className="flex h-full flex-col">
          {/* Workspace header */}
          <div className="flex h-12 shrink-0 items-center gap-2 border-b border-zinc-200 px-4">
            <div className="flex size-6 items-center justify-center rounded-md bg-linear-to-br from-indigo-500 to-indigo-700">
              <SafetyOutlined style={{ color: '#fff', fontSize: 13 }} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-semibold leading-tight text-zinc-900">
                AI Code Reviewer
              </div>
              <div className="flex items-center gap-1.5 leading-tight">
                <span
                  aria-hidden
                  className="inline-block size-1.5 shrink-0 rounded-full"
                  style={{
                    background: !health ? '#D4D4D8' : healthy ? '#10B981' : '#EF4444',
                  }}
                />
                <span className="truncate text-[11px] text-zinc-500">
                  {`production${versionLabel}`}
                </span>
              </div>
            </div>
          </div>

          {/* Nav */}
          <Menu
            mode="inline"
            items={menuItems}
            selectedKeys={[activePage]}
            onClick={({ key }) => onNavigate(key as PageKey)}
            className="min-h-0 flex-1 overflow-y-auto"
            style={{ borderInlineEnd: 'none', paddingTop: 8 }}
          />

          {/* User footer */}
          <div className="shrink-0 border-t border-zinc-200 p-2">
            <Dropdown
              trigger={['click']}
              menu={{
                items: [{ key: 'logout', icon: <LogoutOutlined />, label: '退出登录' }],
                onClick: handleUserMenuClick,
              }}
            >
              <button type="button" className="flex w-full items-center gap-2 rounded-md p-1.5 text-left transition-colors hover:bg-zinc-50">
                <div className="flex size-6 shrink-0 items-center justify-center rounded-full bg-indigo-500 text-[11px] font-medium text-white">
                  {(currentUser?.display_name || currentUser?.username || 'A').charAt(0).toUpperCase()}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] font-medium leading-tight text-zinc-900">
                    {currentUser?.display_name || currentUser?.username || 'admin'}
                  </div>
                  <div className="truncate text-[11px] leading-tight text-zinc-500">
                    {currentUser?.username ? `@${currentUser.username}` : 'Bearer Token'}
                  </div>
                </div>
                <TeamOutlined className="shrink-0 text-[11px] text-zinc-400" />
              </button>
            </Dropdown>
          </div>
        </div>
      </Layout.Sider>

      <Layout style={{ height: '100vh' }}>
        <Layout.Header
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            borderBottom: '1px solid #E4E4E7',
          }}
        >
          <Breadcrumb
            items={[{ title: '工作台' }, { title: currentLabel }]}
            className="text-[13px]"
          />

          <div className="flex-1" />

          {/* 真搜索：点击或 ⌘K 打开命令面板，服务端跨实体查询 */}
          <button
            type="button"
            aria-label="搜索"
            onClick={() => setSearchOpen(true)}
            className="flex h-8 w-56 items-center gap-2 rounded-md border border-[#E4E4E7] bg-white px-2.5 text-left text-[13px] text-zinc-400 transition-colors hover:border-[#D4D4D8]"
          >
            <SearchOutlined className="shrink-0" />
            <span className="flex-1 truncate">搜索…</span>
            <kbd className="rounded border border-[#E4E4E7] bg-[#F4F4F5] px-1.5 py-0.5 font-mono text-[11px] text-zinc-500">
              {isMac ? '⌘K' : 'Ctrl K'}
            </kbd>
          </button>

          {/* 真通知：误报待审核数角标，点击进入误报队列 */}
          <Badge count={pendingFpCount ?? 0} size="small" offset={[2, -2]}>
            <Button
              type="text"
              aria-label="通知"
              icon={<BellOutlined />}
              onClick={() => onNavigate('falsePositives')}
            />
          </Badge>
        </Layout.Header>

        <Layout.Content style={{ overflowY: 'auto' }}>
          <div style={{ padding: 24 }}>{children}</div>
        </Layout.Content>
      </Layout>

      <GlobalSearchModal
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onNavigate={handleSearchNavigate}
        navItems={ALL_NAV_ITEMS.filter((item) => hasPermission(item.key)).map((item) => ({
          key: item.key,
          label: item.label,
          icon: item.icon,
        }))}
      />
    </Layout>
  );
}
