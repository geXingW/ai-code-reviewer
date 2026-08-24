/**
 * 「用户管理」页：管理登录账号、角色归属与可见项目（PR-2 RBAC）。
 *
 * 页面结构：顶部标题 + 右上角「新建用户」按钮 + 分页表格。
 * 表格列：用户名 | 显示名 | 角色 | 分配项目数 | 状态 | 操作。
 * 新建 / 编辑走同一个弹窗（用户名、密码、显示名、角色下拉、项目多选、启用开关），
 * 项目多选用原生 checkbox（项目来自 App 透传的 projects）。系统内置用户
 * （username === 'admin'）不可删除。
 */

import { useEffect, useState } from 'react';
import { Pencil, Trash2, UserPlus, Users } from 'lucide-react';

import {
  ProjectConfig,
  Role,
  User,
  assignProjects,
  createUser,
  deleteUser,
  fetchRoles,
  fetchUsers,
  updateUser,
} from '../api';
import { Button } from '../components/ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from '../components/ui/card';
import { Dialog } from '../components/ui/dialog';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Select } from '../components/ui/select';
import { EmptyState } from '../components/EmptyState';

export interface UsersPageProps {
  /** 从 App 透传已加载的项目列表，用于弹窗里的项目多选 checkbox。 */
  projects: ProjectConfig[];
}

const PAGE_SIZE = 20;

type UserDialog =
  | { mode: 'create' }
  | { mode: 'edit'; data: User }
  | null;

type UserFormState = {
  username: string;
  password: string;
  display_name: string;
  role_id: string;
  enabled: boolean;
  project_ids: string[];
};

function emptyForm(): UserFormState {
  return {
    username: '',
    password: '',
    display_name: '',
    role_id: '',
    enabled: true,
    project_ids: [],
  };
}

function toForm(user: User): UserFormState {
  return {
    username: user.username,
    password: '',
    display_name: user.display_name ?? '',
    role_id: user.role_id ?? '',
    enabled: user.enabled,
    project_ids: [...user.assigned_project_ids],
  };
}

/** 系统内置用户（PR-1 种子数据），不可删除。 */
function isSystemAdmin(user: User): boolean {
  return user.username === 'admin';
}

export function UsersPage({ projects }: UsersPageProps) {
  const [users, setUsers] = useState<User[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<UserDialog>(null);
  const [page, setPage] = useState({ offset: 0, limit: PAGE_SIZE, total: 0 });

  // 拉角色下拉所需的全量角色列表（limit 用大值一次拉满）。
  useEffect(() => {
    let active = true;
    fetchRoles(0, 200)
      .then((data) => {
        if (active) {
          setRoles(data.items);
        }
      })
      .catch(() => {
        // 角色加载失败不阻塞页面，弹窗下拉回退为"未分配"。
      });
    return () => {
      active = false;
    };
  }, []);

  // 分页加载用户列表，offset / total 变化时重新拉取。
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchUsers(page.offset, page.limit)
      .then((data) => {
        if (active) {
          setUsers(data.items);
          setPage((prev) => ({ ...prev, total: data.total }));
        }
      })
      .catch((caught) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : '加载用户列表失败');
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [page.offset, page.limit]);

  async function handleDelete(user: User) {
    if (isSystemAdmin(user)) {
      return;
    }
    if (!window.confirm(`确定删除用户「${user.username}」？该操作不可撤销。`)) {
      return;
    }
    setError(null);
    try {
      await deleteUser(user.id);
      setUsers((prev) => prev.filter((item) => item.id !== user.id));
      setPage((prev) => ({ ...prev, total: Math.max(0, prev.total - 1) }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '删除用户失败');
    }
  }

  async function handleSubmit(form: UserFormState) {
    setError(null);
    try {
      if (dialog?.mode === 'edit') {
        const user = dialog.data;
        const updated = await updateUser(user.id, {
          username: form.username.trim() || undefined,
          display_name: form.display_name.trim() || undefined,
          role_id: form.role_id || undefined,
          enabled: form.enabled,
          // 密码留空表示不修改。
          ...(form.password ? { password: form.password } : {}),
        });
        // 项目分配走独立接口。
        await assignProjects(user.id, { project_ids: form.project_ids });
        setUsers((prev) =>
          prev.map((item) =>
            item.id === updated.id ? { ...updated, assigned_project_ids: form.project_ids } : item,
          ),
        );
      } else {
        const created = await createUser({
          username: form.username.trim(),
          password: form.password,
          display_name: form.display_name.trim() || undefined,
          role_id: form.role_id || undefined,
          enabled: form.enabled,
        });
        await assignProjects(created.id, { project_ids: form.project_ids });
        setUsers((prev) => [{ ...created, assigned_project_ids: form.project_ids }, ...prev]);
        setPage((prev) => ({ ...prev, total: prev.total + 1 }));
      }
      setDialog(null);
    } catch (caught) {
      // 抛给弹窗展示行内错误，同时父页保留 banner。
      setError(caught instanceof Error ? caught.message : '保存用户失败');
      throw caught;
    }
  }

  const pageCount = Math.max(1, Math.ceil(page.total / page.limit));
  const currentPage = Math.floor(page.offset / page.limit) + 1;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>用户管理</CardTitle>
            <CardDescription>
              共 {page.total} 个用户 · 管理登录账号、角色与可见项目
            </CardDescription>
          </div>
          <Button size="sm" onClick={() => setDialog({ mode: 'create' })}>
            <UserPlus className="size-3.5" />
            新建用户
          </Button>
        </CardHeader>

        {error ? (
          <div
            className="m-4 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            role="alert"
          >
            {error}
          </div>
        ) : null}

        {loading ? (
          <div className="p-6 text-center text-[13px] text-zinc-500">加载中…</div>
        ) : users.length === 0 ? (
          <EmptyState
            icon={Users}
            title="暂无用户"
            description="创建账号后即可分配角色与可见项目"
            action={(
              <Button size="sm" onClick={() => setDialog({ mode: 'create' })}>
                <UserPlus className="size-3.5" />
                新建用户
              </Button>
            )}
          />
        ) : (
          <div className="divide-y divide-zinc-100">
            <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,0.6fr)_minmax(0,0.8fr)_110px] items-center gap-2 px-4 py-2 text-[10px] font-medium uppercase text-zinc-400">
              <div>用户名</div>
              <div>显示名</div>
              <div>角色</div>
              <div>分配项目数</div>
              <div>状态</div>
              <div>最近更新</div>
              <div className="text-right">操作</div>
            </div>
            {users.map((user) => {
              const systemAdmin = isSystemAdmin(user);
              return (
                <div
                  key={user.id}
                  className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,0.6fr)_minmax(0,0.8fr)_110px] items-center gap-2 px-4 py-2.5 text-[13px] hover:bg-zinc-50 transition-colors"
                >
                  <div className="flex items-center gap-1.5 truncate font-mono text-zinc-900">
                    {user.username}
                    {systemAdmin ? (
                      <span className="ml-1 rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-600">
                        内置
                      </span>
                    ) : null}
                  </div>
                  <div className="truncate text-zinc-700">{user.display_name ?? '-'}</div>
                  <div className="truncate text-zinc-700">{user.role_name ?? '未分配'}</div>
                  <div className="text-zinc-500">{user.assigned_project_ids.length}</div>
                  <div>
                    <span
                      className={
                        user.enabled
                          ? 'inline-flex rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-600'
                          : 'inline-flex rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] font-medium text-zinc-500'
                      }
                    >
                      {user.enabled ? '启用' : '停用'}
                    </span>
                  </div>
                  <div className="truncate text-zinc-500">
                    {user.updated_at ? new Date(user.updated_at).toLocaleDateString() : '-'}
                  </div>
                  <div className="flex items-center justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      type="button"
                      title={systemAdmin ? '内置用户可编辑但不可删除' : `编辑 ${user.username}`}
                      onClick={() => setDialog({ mode: 'edit', data: user })}
                    >
                      <Pencil className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      type="button"
                      className="text-red-600 hover:bg-red-50 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-40"
                      disabled={systemAdmin}
                      title={systemAdmin ? '系统内置用户不可删除' : undefined}
                      onClick={() => void handleDelete(user)}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>
              );
            })}

            {page.total > page.limit ? (
              <div className="flex items-center justify-between px-4 py-3">
                <div className="text-[12px] text-zinc-500">
                  第 {currentPage} / {pageCount} 页 · 共 {page.total} 条
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={page.offset <= 0}
                    onClick={() => setPage((prev) => ({ ...prev, offset: Math.max(0, prev.offset - prev.limit) }))}
                  >
                    上一页
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={page.offset + page.limit >= page.total}
                    onClick={() => setPage((prev) => ({ ...prev, offset: prev.offset + prev.limit }))}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </Card>

      <UserDialogForm
        open={dialog !== null}
        dialog={dialog}
        roles={roles}
        projects={projects}
        onCancel={() => setDialog(null)}
        onSubmit={handleSubmit}
      />
    </div>
  );
}

interface UserDialogFormProps {
  open: boolean;
  dialog: UserDialog;
  roles: Role[];
  projects: ProjectConfig[];
  onCancel: () => void;
  onSubmit: (form: UserFormState) => Promise<void>;
}

/** 新建 / 编辑用户弹窗（纯受控组件，接口调用交给父组件）。 */
function UserDialogForm({ open, dialog, roles, projects, onCancel, onSubmit }: UserDialogFormProps) {
  const [form, setForm] = useState<UserFormState>(emptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const isEditMode = dialog?.mode === 'edit';

  // dialog 每次重新打开都重置表单（编辑模式回填数据）。
  useEffect(() => {
    if (open) {
      setForm(dialog?.mode === 'edit' ? toForm(dialog.data) : emptyForm());
      setErrorMessage(null);
      setSubmitting(false);
    }
    // dialog 引用变化即视为重新打开，依赖里带上它。
  }, [open, dialog]);

  const canSubmit =
    !submitting &&
    form.username.trim().length > 0 &&
    (isEditMode || form.password.trim().length > 0); // 新建时密码必填

  function toggleProject(projectId: string) {
    setForm((prev) => {
      const has = prev.project_ids.includes(projectId);
      return {
        ...prev,
        project_ids: has
          ? prev.project_ids.filter((item) => item !== projectId)
          : [...prev.project_ids, projectId],
      };
    });
  }

  async function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    try {
      await onSubmit(form);
    } catch (caught) {
      setErrorMessage(caught instanceof Error ? caught.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={submitting ? () => {} : onCancel}
      title={isEditMode ? `编辑用户 · ${dialog?.data.username}` : '新建用户'}
      subtitle={isEditMode ? '可修改登录信息、角色、启停以及可见项目' : '创建登录账号并分配角色与可见项目'}
      footer={
        <>
          <Button type="button" variant="secondary" size="sm" disabled={submitting} onClick={onCancel}>
            取消
          </Button>
          <Button type="button" size="sm" disabled={!canSubmit} onClick={() => void handleSubmit()}>
            {submitting ? '保存中…' : '保存'}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="user-username">用户名</Label>
            <Input
              id="user-username"
              value={form.username}
              onChange={(event) => setForm({ ...form, username: event.target.value })}
              placeholder={isEditMode ? '留空表示不修改' : '例如：alice'}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="user-password">密码</Label>
            <Input
              id="user-password"
              type="password"
              value={form.password}
              onChange={(event) => setForm({ ...form, password: event.target.value })}
              placeholder={isEditMode ? '留空表示不修改' : '必填'}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="user-display-name">显示名（选填）</Label>
            <Input
              id="user-display-name"
              value={form.display_name}
              onChange={(event) => setForm({ ...form, display_name: event.target.value })}
              placeholder="选填"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="user-role">角色</Label>
            <Select
              id="user-role"
              aria-label="选择角色"
              value={form.role_id}
              onChange={(event) => setForm({ ...form, role_id: event.target.value })}
            >
              <option value="">未分配</option>
              {roles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                </option>
              ))}
            </Select>
          </div>
        </div>

        <div className="space-y-1.5">
          <Label>可见项目</Label>
          {projects.length === 0 ? (
            <div className="rounded-md bg-zinc-50 px-3 py-2 text-[12px] text-zinc-500">
              暂无项目。请先在「GitLab 项目」页添加项目。
            </div>
          ) : (
            <div className="grid max-h-40 grid-cols-2 gap-x-3 gap-y-1.5 overflow-y-auto rounded-md border border-[#E4E4E7] p-2.5">
              {projects.map((project) => {
                const checked = form.project_ids.includes(project.id);
                return (
                  <label
                    key={project.id}
                    className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-[13px] hover:bg-zinc-50"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleProject(project.id)}
                      className="size-4 rounded border-zinc-300 accent-indigo-600"
                    />
                    <span className="truncate text-zinc-700">{project.name}</span>
                  </label>
                );
              })}
            </div>
          )}
        </div>

        <label className="flex cursor-pointer items-center justify-between rounded-md border border-[#E4E4E7] px-3 py-2">
          <span className="text-[13px] text-zinc-700">启用该用户</span>
          <input
            type="checkbox"
            role="switch"
            checked={form.enabled}
            onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
            className="size-4 accent-indigo-600"
          />
        </label>
      </div>

      {errorMessage ? (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-700" role="alert">
          {errorMessage}
        </div>
      ) : null}
    </Dialog>
  );
}