/**
 * 「角色管理」页：管理 RBAC 角色及其权限集合（PR-2 RBAC）。
 *
 * 页面结构：顶部标题 + 右上角「新建角色」按钮 + 分页表格。
 * 表格列：角色名 | 描述 | 权限数 | 系统角色 | 操作。
 * 新建 / 编辑走同一个弹窗（角色名、描述、按分组勾选权限）。系统内置
 * 角色（is_system === true）不可删除，删除按钮 disabled + tooltip 提示。
 * 该页无外部依赖，不需要 App 透传 projects。
 */

import { useEffect, useState } from 'react';
import { Pencil, Shield, ShieldPlus, Trash2 } from 'lucide-react';

import { Role, createRole, deleteRole, fetchRoles, updateRole } from '../api';
import { Button } from '../components/ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from '../components/ui/card';
import { Dialog } from '../components/ui/dialog';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { EmptyState } from '../components/EmptyState';

const PAGE_SIZE = 50;

/** 权限的栅格名称，仅在表格列头使用。 */
const MENU_PERMISSIONS = [
  'page:dashboard',
  'page:providers',
  'page:global-prompt',
  'page:rules',
  'page:projects',
  'page:user-mappings',
  'page:reviews',
  'page:findings',
  'page:falsePositives',
  'page:negativeExamples',
  'page:engines',
  'page:users',
  'page:roles',
];

const ACTION_PERMISSIONS = [
  'user:create',
  'user:edit',
  'user:delete',
  'role:create',
  'role:edit',
  'role:delete',
  'project:assign',
];

/** 权限 key -> 中文 label，用于弹窗内勾选项与「权限数」hover 明细。 */
const PERMISSION_LABELS: Record<string, string> = {
  'page:dashboard': '仪表盘',
  'page:providers': '模型供应商',
  'page:global-prompt': '全局提示词',
  'page:rules': '审查规则',
  'page:projects': 'GitLab 项目',
  'page:user-mappings': '用户映射',
  'page:reviews': '审查记录',
  'page:findings': '问题与误报',
  'page:falsePositives': '误报队列',
  'page:negativeExamples': '负样本库',
  'page:engines': '引擎配置',
  'page:users': '用户管理',
  'page:roles': '角色管理',
  'user:create': '创建用户',
  'user:edit': '编辑用户',
  'user:delete': '删除用户',
  'role:create': '创建角色',
  'role:edit': '编辑角色',
  'role:delete': '删除角色',
  'project:assign': '分配项目',
};

function permissionKeyLabel(key: string): string {
  return PERMISSION_LABELS[key] ?? key;
}

type RoleDialog =
  | { mode: 'create' }
  | { mode: 'edit'; data: Role }
  | null;

type RoleFormState = {
  name: string;
  description: string;
  permissions: string[];
};

function emptyForm(): RoleFormState {
  return { name: '', description: '', permissions: [] };
}

function toForm(role: Role): RoleFormState {
  return {
    name: role.name,
    description: role.description ?? '',
    permissions: [...role.permissions],
  };
}

export function RolesPage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<RoleDialog>(null);
  const [page, setPage] = useState({ offset: 0, limit: PAGE_SIZE, total: 0 });

  // 分页加载角色列表，offset / total 变化时重新拉取。
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchRoles(page.offset, page.limit)
      .then((data) => {
        if (active) {
          setRoles(data.items);
          setPage((prev) => ({ ...prev, total: data.total }));
        }
      })
      .catch((caught) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : '加载角色列表失败');
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

  async function handleDelete(role: Role) {
    if (role.is_system) {
      return;
    }
    if (!window.confirm(`确定删除角色「${role.name}」？该操作不可撤销。`)) {
      return;
    }
    setError(null);
    try {
      await deleteRole(role.id);
      setRoles((prev) => prev.filter((item) => item.id !== role.id));
      setPage((prev) => ({ ...prev, total: Math.max(0, prev.total - 1) }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '删除角色失败');
    }
  }

  async function handleSubmit(form: RoleFormState) {
    setError(null);
    try {
      if (dialog?.mode === 'edit') {
        const role = dialog.data;
        const updated = await updateRole(role.id, {
          name: form.name.trim() || undefined,
          description: form.description.trim() || undefined,
          permissions: form.permissions,
        });
        setRoles((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      } else {
        const created = await createRole({
          name: form.name.trim(),
          description: form.description.trim() || undefined,
          permissions: form.permissions,
        });
        setRoles((prev) => [created, ...prev]);
        setPage((prev) => ({ ...prev, total: prev.total + 1 }));
      }
      setDialog(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '保存角色失败');
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
            <CardTitle>角色管理</CardTitle>
            <CardDescription>
              共 {page.total} 个角色 · 定义权限集合并分派给用户
            </CardDescription>
          </div>
          <Button size="sm" onClick={() => setDialog({ mode: 'create' })}>
            <ShieldPlus className="size-3.5" />
            新建角色
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
        ) : roles.length === 0 ? (
          <EmptyState
            icon={Shield}
            title="暂无角色"
            description="创建角色后即可批量分配权限给用户"
            action={(
              <Button size="sm" onClick={() => setDialog({ mode: 'create' })}>
                <ShieldPlus className="size-3.5" />
                新建角色
              </Button>
            )}
          />
        ) : (
          <div className="divide-y divide-zinc-100">
            <div className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,2fr)_minmax(0,0.7fr)_minmax(0,0.7fr)_80px] items-center gap-2 px-4 py-2 text-[10px] font-medium uppercase text-zinc-400">
              <div>角色名</div>
              <div>描述</div>
              <div>权限数</div>
              <div>系统角色</div>
              <div className="text-right">操作</div>
            </div>
            {roles.map((role) => (
              <div
                key={role.id}
                className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,2fr)_minmax(0,0.7fr)_minmax(0,0.7fr)_80px] items-center gap-2 px-4 py-2.5 text-[13px] hover:bg-zinc-50 transition-colors"
              >
                <div className="flex items-center gap-1.5 truncate font-mono text-zinc-900">
                  {role.name}
                  {role.is_system ? (
                    <span className="ml-1 rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-600">
                      内置
                    </span>
                  ) : null}
                </div>
                <div className="truncate text-zinc-700">{role.description ?? '-'}</div>
                <div
                  className="truncate text-zinc-500"
                  title={role.permissions.map(permissionKeyLabel).join('、')}
                >
                  {role.permissions.length}
                </div>
                <div className="text-zinc-500">{role.is_system ? '是' : '否'}</div>
                <div className="flex items-center justify-end gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    type="button"
                    title={role.is_system ? '内置角色可编辑但不可删除' : `编辑 ${role.name}`}
                    onClick={() => setDialog({ mode: 'edit', data: role })}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    type="button"
                    className="text-red-600 hover:bg-red-50 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={role.is_system}
                    title={role.is_system ? '系统内置角色不可删除' : undefined}
                    onClick={() => void handleDelete(role)}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
            ))}

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

      <RoleDialogForm
        open={dialog !== null}
        dialog={dialog}
        onCancel={() => setDialog(null)}
        onSubmit={handleSubmit}
      />
    </div>
  );
}

interface RoleDialogFormProps {
  open: boolean;
  dialog: RoleDialog;
  onCancel: () => void;
  onSubmit: (form: RoleFormState) => Promise<void>;
}

/** 新建 / 编辑角色弹窗（纯受控组件，接口调用交给父组件）。 */
function RoleDialogForm({ open, dialog, onCancel, onSubmit }: RoleDialogFormProps) {
  const [form, setForm] = useState<RoleFormState>(emptyForm);
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
    !submitting && form.name.trim().length > 0;

  function togglePermission(permission: string) {
    setForm((prev) => {
      const has = prev.permissions.includes(permission);
      return {
        ...prev,
        permissions: has
          ? prev.permissions.filter((item) => item !== permission)
          : [...prev.permissions, permission],
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
      title={isEditMode ? `编辑角色 · ${dialog?.data.name}` : '新建角色'}
      subtitle={isEditMode ? '可修改角色名、描述与权限集合' : '创建角色并勾选其拥有的权限'}
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
            <Label htmlFor="role-name">角色名</Label>
            <Input
              id="role-name"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="例如：reviewer"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="role-description">描述（选填）</Label>
            <Input
              id="role-description"
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
              placeholder="选填"
            />
          </div>
        </div>

        <div className="space-y-2">
          <Label>权限</Label>

          <PermissionGroup
            title="菜单权限"
            permissions={MENU_PERMISSIONS}
            labels={PERMISSION_LABELS}
            selected={form.permissions}
            onToggle={togglePermission}
          />

          <PermissionGroup
            title="操作权限"
            permissions={ACTION_PERMISSIONS}
            labels={PERMISSION_LABELS}
            selected={form.permissions}
            onToggle={togglePermission}
          />
        </div>
      </div>

      {errorMessage ? (
        <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-700" role="alert">
          {errorMessage}
        </div>
      ) : null}
    </Dialog>
  );
}

interface PermissionGroupProps {
  title: string;
  permissions: string[];
  labels: Record<string, string>;
  selected: string[];
  onToggle: (permission: string) => void;
}

/** 权限分组 UI：一个分组渲染为一组原生的 checkbox（复刻「菜单权限 / 操作权限」结构）。 */
function PermissionGroup({ title, permissions, labels, selected, onToggle }: PermissionGroupProps) {
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] font-medium text-zinc-500">{title}</div>
      <div className="grid grid-cols-3 gap-x-3 gap-y-1.5 rounded-md border border-[#E4E4E7] p-2.5">
        {permissions.map((permission) => {
          const checked = selected.includes(permission);
          return (
            <label
              key={permission}
              className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-[12px] hover:bg-zinc-50"
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => onToggle(permission)}
                className="size-4 rounded border-zinc-300 accent-indigo-600"
              />
              <span className="truncate text-zinc-700">{labels[permission] ?? permission}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}