/**
 * 「用户管理」页（antd 版）：管理登录账号、角色归属与可见项目（PR-2 RBAC）。
 *
 * - 表格走 antd Table 服务端分页（fetchUsers offset/limit）；
 * - 角色下拉改用 fetchRolesAll() 循环翻页拉全量——此前一次传 limit=200
 *   超过 后端 le=100 上限而 422，又被 .catch(() => {}) 静默吞掉，
 *   导致下拉只剩「未分配」；
 * - 项目列表本页自取（fetchProjectsAll），不再由 App 透传。
 */

import { useEffect, useState } from 'react';
import { PlusOutlined } from '@ant-design/icons';
import { App as AntApp, Alert, Button, Card, Checkbox, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  assignProjects,
  createUser,
  deleteUser,
  fetchProjectsAll,
  fetchRolesAll,
  fetchUsers,
  isAuthRequiredError,
  updateUser,
  type ProjectConfig,
  type Role,
  type User,
} from '../api';
import { usePagedList } from '../hooks/usePagedList';

type UserDialog = { mode: 'create' } | { mode: 'edit'; data: User } | null;

type UserFormState = {
  username: string;
  password: string;
  display_name: string;
  role_id: string;
  enabled: boolean;
  project_ids: string[];
};

function emptyForm(): UserFormState {
  return { username: '', password: '', display_name: '', role_id: '', enabled: true, project_ids: [] };
}

function toForm(user: User): UserFormState {
  return {
    username: user.username,
    password: '',
    display_name: user.display_name ?? '',
    role_id: user.role_id ?? '',
    enabled: user.enabled,
    project_ids: [...(user.project_ids ?? [])],
  };
}

/** 系统内置用户（PR-1 种子数据），不可删除。 */
function isSystemAdmin(user: User): boolean {
  return user.username === 'admin';
}

export function UsersPage() {
  const { message } = AntApp.useApp();
  const [roles, setRoles] = useState<Role[]>([]);
  const [projects, setProjects] = useState<ProjectConfig[]>([]);
  const [dialog, setDialog] = useState<UserDialog>(null);

  useEffect(() => {
    let active = true;
    // 角色下拉：循环翻页拉全量（修复原 limit=200 → 422 静默失败）。
    fetchRolesAll()
      .then((page) => {
        if (active) setRoles(page.items);
      })
      .catch(() => {
        // 角色加载失败不阻塞页面，弹窗下拉回退为「未分配」。
      });
    fetchProjectsAll()
      .then((page) => {
        if (active) setProjects(page.items);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  const { items, total, loading, error, reload, pagination } = usePagedList<User, Record<string, never>>(
    ({ limit, offset }) => fetchUsers(offset, limit),
    {},
  );

  async function handleDelete(user: User) {
    try {
      await deleteUser(user.id);
      message.success('用户已删除。');
      reload();
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '删除用户失败');
      }
    }
  }

  const roleNameById = new Map(roles.map((role) => [role.id, role.name]));

  const columns: ColumnsType<User> = [
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      width: 180,
      render: (value: string, record) => (
        <span className="font-mono text-zinc-900">
          {value}
          {isSystemAdmin(record) ? (
            <Tag color="blue" className="ml-1.5">
              内置
            </Tag>
          ) : null}
        </span>
      ),
    },
    {
      title: '显示名',
      dataIndex: 'display_name',
      key: 'display_name',
      ellipsis: true,
      render: (value: string | null) => value ?? '-',
    },
    {
      title: '角色',
      key: 'role',
      width: 140,
      render: (_, record) => record.role_name ?? roleNameById.get(record.role_id ?? '') ?? '未分配',
    },
    {
      title: '分配项目数',
      key: 'project_count',
      width: 110,
      render: (_, record) => <span className="text-zinc-500">{record.project_ids?.length ?? 0}</span>,
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 90,
      render: (value: boolean) => (value ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
    },
    {
      title: '最近更新',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 120,
      render: (value: string) => (
        <span className="text-zinc-500">
          {value ? new Date(value).toLocaleDateString() : '-'}
        </span>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 130,
      render: (_, record) => {
        const systemAdmin = isSystemAdmin(record);
        return (
          <Space>
            <Button
              type="link"
              size="small"
              title={systemAdmin ? '内置用户可编辑但不可删除' : `编辑 ${record.username}`}
              onClick={() => setDialog({ mode: 'edit', data: record })}
            >
              编辑
            </Button>
            <Popconfirm
              title={`确定删除用户「${record.username}」？`}
              description="该操作不可撤销。"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              disabled={systemAdmin}
              onConfirm={() => void handleDelete(record)}
            >
              <Button type="link" size="small" danger disabled={systemAdmin}
                title={systemAdmin ? '系统内置用户不可删除' : undefined}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  return (
    <Card
      title="用户管理"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setDialog({ mode: 'create' })}>
          新建用户
        </Button>
      }
      styles={{ body: { paddingTop: 0 } }}
    >
      {error ? <Alert type="error" showIcon message={error} className="mb-3" /> : null}

      <Table<User>
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={pagination}
        locale={{ emptyText: '暂无用户 · 创建账号后即可分配角色与可见项目' }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">共 {total} 个用户</div>

      <UserDialogForm
        open={dialog !== null}
        dialog={dialog}
        roles={roles}
        projects={projects}
        onCancel={() => setDialog(null)}
        onSubmit={async (form) => {
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
              void updated;
            } else {
              const created = await createUser({
                username: form.username.trim(),
                password: form.password,
                display_name: form.display_name.trim() || undefined,
                role_id: form.role_id || undefined,
                enabled: form.enabled,
              });
              await assignProjects(created.id, { project_ids: form.project_ids });
            }
            message.success(dialog?.mode === 'edit' ? '用户已更新。' : '用户已创建。');
            setDialog(null);
            reload();
          } catch (caught) {
            if (!isAuthRequiredError(caught)) {
              message.error(caught instanceof Error ? caught.message : '保存用户失败');
            }
            throw caught;
          }
        }}
      />
    </Card>
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

  useEffect(() => {
    if (open) {
      setForm(dialog?.mode === 'edit' ? toForm(dialog.data) : emptyForm());
      setErrorMessage(null);
      setSubmitting(false);
    }
  }, [open, dialog]);

  const canSubmit =
    !submitting && form.username.trim().length > 0 && (isEditMode || form.password.trim().length > 0);

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
    <Modal
      open={open}
      onCancel={submitting ? undefined : onCancel}
      title={isEditMode ? `编辑用户 · ${dialog?.data.username}` : '新建用户'}
      okText="保存"
      cancelText="取消"
      okButtonProps={{ disabled: !canSubmit, loading: submitting }}
      cancelButtonProps={{ disabled: submitting }}
      onOk={() => void handleSubmit()}
      maskClosable={false}
      destroyOnHidden
    >
      <div className="space-y-3 pt-2">
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="user-username">
              用户名
            </label>
            <Input
              id="user-username"
              value={form.username}
              onChange={(event) => setForm({ ...form, username: event.target.value })}
              placeholder={isEditMode ? '留空表示不修改' : '例如：alice'}
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="user-password">
              密码
            </label>
            <Input.Password
              id="user-password"
              value={form.password}
              onChange={(event) => setForm({ ...form, password: event.target.value })}
              placeholder={isEditMode ? '留空表示不修改' : '必填'}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="user-display-name">
              显示名（选填）
            </label>
            <Input
              id="user-display-name"
              value={form.display_name}
              onChange={(event) => setForm({ ...form, display_name: event.target.value })}
              placeholder="选填"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="user-role">
              角色
            </label>
            <Select
              id="user-role"
              aria-label="选择角色"
              value={form.role_id || undefined}
              placeholder="未分配"
              allowClear
              style={{ width: '100%' }}
              options={roles.map((role) => ({ value: role.id, label: role.name }))}
              onChange={(value) => setForm({ ...form, role_id: value ?? '' })}
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <span className="text-[12px] font-medium text-zinc-600">可见项目</span>
          {projects.length === 0 ? (
            <div className="rounded-md bg-zinc-50 px-3 py-2 text-[12px] text-zinc-500">
              暂无项目。请先在「GitLab 项目」页添加项目。
            </div>
          ) : (
            <Checkbox.Group
              className="grid max-h-40 grid-cols-2 gap-x-3 gap-y-1.5 overflow-y-auto rounded-md border border-zinc-200 p-2.5"
              value={form.project_ids}
              onChange={(values) => setForm({ ...form, project_ids: values as string[] })}
              options={projects.map((project) => ({ value: project.id, label: project.name }))}
            />
          )}
        </div>

        <div className="flex items-center justify-between rounded-md border border-zinc-200 px-3 py-2">
          <span className="text-[13px] text-zinc-700">启用该用户</span>
          <Switch
            checked={form.enabled}
            onChange={(checked) => setForm({ ...form, enabled: checked })}
            aria-label="启用该用户"
          />
        </div>

        {errorMessage ? (
          <Alert type="error" showIcon message={errorMessage} />
        ) : null}
      </div>
    </Modal>
  );
}
