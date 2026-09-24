/**
 * 应用壳（antd 版）：ConfigProvider 主题 + 登录态 + 页面路由。
 *
 * 此前的 App.tsx 约 2800 行、13 个页面全部内联，列表数据集中在本组件拉取，
 * 导致大多数列表只显示后端默认前 20 条。现在每个页面自治（各自拉数、各自
 * 服务端分页/筛选），本组件只负责：
 * - antd 主题（theme.ts）与中文 locale；
 * - 登录态（sessionStorage token + 当前用户恢复）与健康检查；
 * - HashRouter URL 同步的页面切换，以及 ⌘K 搜索「带初始筛选跳转」的意图透传。
 */

import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { App as AntApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';

import {
  clearStoredAdminAccessToken,
  fetchHealth,
  getStoredAdminAccessToken,
  getStoredAdminDisplayName,
  getStoredAdminPermissions,
  getStoredAdminProjectIds,
  getStoredAdminUsername,
  type CurrentUser,
  type HealthStatus,
} from './api';
import { antdTheme } from './theme';
import { ALL_NAV_ITEMS, AppShell, type PageKey } from './components/layout/AppShell';
import { LoginPage } from './pages/LoginPage';
import { DashboardPage } from './pages/DashboardPage';
import { ReviewRecordsPage } from './pages/ReviewRecordsPage';
import { FindingsPage } from './pages/FindingsPage';
import { FalsePositivesPage } from './pages/FalsePositivesPage';
import { NegativeExamplesPage } from './pages/NegativeExamplesPage';
import { ProvidersPage } from './pages/ProvidersPage';
import { RulesPage } from './pages/RulesPage';
import { ProjectsPage } from './pages/ProjectsPage';
import { EnginesPage } from './pages/EnginesPage';
import { GlobalPromptPage } from './pages/GlobalPromptPage';
import { UserMappingsPage } from './pages/UserMappingsPage';
import { UsersPage } from './pages/UsersPage';
import { RolesPage } from './pages/RolesPage';

function AppInner() {
  const navigate = useNavigate();
  const location = useLocation();
  const [adminToken, setAdminToken] = useState(() => getStoredAdminAccessToken());
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  // ⌘K 搜索「带筛选跳转」的意图：nonce 变化时目标页面以 initialFilters 重挂载。
  const [pageIntent, setPageIntent] = useState<{ nonce: number; filters: Record<string, string> } | null>(
    null,
  );

  // 从 URL path 读取初始页面（/dashboard, /providers, ...），刷新保持不变
  const getInitialPage = (): PageKey => {
    const path = location.pathname.replace(/^\//, '') as PageKey;
    if (path && ALL_NAV_ITEMS.some((item) => item.key === path)) {
      return path;
    }
    return 'dashboard';
  };

  const [activePage, setActivePageState] = useState<PageKey>(getInitialPage);

  const navigateTo = useCallback(
    (page: PageKey) => {
      setActivePageState(page);
      navigate(`/${page}`, { replace: true });
    },
    [navigate],
  );

  // URL 变化（浏览器后退/前进）时同步 activePage
  useEffect(() => {
    const path = location.pathname.replace(/^\//, '') as PageKey;
    if (path && ALL_NAV_ITEMS.some((item) => item.key === path)) {
      setActivePageState(path);
    }
  }, [location.pathname]);

  // token 变化时：恢复当前用户（sessionStorage）并做一次健康检查。
  useEffect(() => {
    if (!adminToken) {
      setHealth(null);
      setCurrentUser(null);
      return;
    }
    setCurrentUser({
      username: getStoredAdminUsername() || '',
      display_name: getStoredAdminDisplayName() || null,
      permissions: getStoredAdminPermissions(),
      project_ids: getStoredAdminProjectIds(),
    });
    let active = true;
    fetchHealth()
      .then((status) => {
        if (active) setHealth(status);
      })
      .catch(() => {
        if (active) setHealth(null);
      });
    return () => {
      active = false;
    };
  }, [adminToken]);

  function handleLogout() {
    clearStoredAdminAccessToken();
    setAdminToken('');
    setCurrentUser(null);
    setActivePageState('dashboard');
    navigate('/dashboard', { replace: true });
  }

  function handleNavigateWithFilters(page: PageKey, filters: Record<string, string>) {
    setPageIntent({ nonce: Date.now(), filters });
    navigateTo(page);
  }

  if (!adminToken) {
    return <LoginPage onSuccess={() => setAdminToken(getStoredAdminAccessToken())} />;
  }

  const intentFilters = pageIntent?.filters;
  // nonce 作为 key：⌘K 携带新筛选跳转时强制重挂目标页，让 initialFilters 生效。
  const intentKey = pageIntent?.nonce ?? 'static';

  return (
    <AppShell
      activePage={activePage}
      onNavigate={navigateTo}
      onNavigateWithFilters={handleNavigateWithFilters}
      health={health}
      onLogout={handleLogout}
      currentUser={
        currentUser ? { username: currentUser.username, display_name: currentUser.display_name } : undefined
      }
      permissions={currentUser?.permissions}
    >
      {activePage === 'dashboard' ? <DashboardPage key={intentKey} health={health} /> : null}
      {activePage === 'reviews' ? (
        <ReviewRecordsPage key={intentKey} initialFilters={intentFilters} />
      ) : null}
      {activePage === 'findings' ? (
        <FindingsPage key={intentKey} initialFilters={intentFilters} />
      ) : null}
      {activePage === 'falsePositives' ? <FalsePositivesPage key={intentKey} /> : null}
      {activePage === 'negativeExamples' ? (
        <NegativeExamplesPage key={intentKey} initialFilters={intentFilters} />
      ) : null}
      {activePage === 'providers' ? <ProvidersPage key={intentKey} initialFilters={intentFilters} /> : null}
      {activePage === 'rules' ? <RulesPage key={intentKey} initialFilters={intentFilters} /> : null}
      {activePage === 'projects' ? <ProjectsPage key={intentKey} initialFilters={intentFilters} /> : null}
      {activePage === 'user-mappings' ? <UserMappingsPage key={intentKey} /> : null}
      {activePage === 'engines' ? <EnginesPage key={intentKey} /> : null}
      {activePage === 'global-prompt' ? <GlobalPromptPage key={intentKey} /> : null}
      {activePage === 'users' ? <UsersPage key={intentKey} /> : null}
      {activePage === 'roles' ? <RolesPage key={intentKey} /> : null}
    </AppShell>
  );
}

function App() {
  return (
    <ConfigProvider theme={antdTheme} locale={zhCN}>
      <AntApp>
        <AppInner />
      </AntApp>
    </ConfigProvider>
  );
}

export default App;
