/**
 * 「角色管理」页（antd 版）：管理 RBAC 角色及其权限集合（PR-2 RBAC）。
 *
 * 表格走 antd Table 服务端分页（fetchRoles offset/limit，默认每页 50）；
 * 新建 / 编辑弹窗按「菜单权限 / 操作权限」分组勾选权限。系统内置角色
 * （is_system === true）不可删除。
 */

import { useEffect, useState } from 'react';
import { PlusOutlined } from '@ant-design/icons';
import { App as AntApp, Alert, Button, Card, Checkbox, Input, Modal, Popconfirm, Space, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  createRole,
  deleteRole,
  fetchRoles,
  isAuthRequiredError,
  updateRole,
  type Role,
} from '../api';
import { usePagedList } from '../hooks/usePagedList';

const PAGE_SIZE = 50;

/** 权限分组清单（与后端种子权限一致）。 */
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

type RoleDialog = { mode: 'create' } | { mode: 'edit'; data: Role } | null;

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
  const { message } = AntApp.useApp();
  const [dialog, setDialog] = useState<RoleDialog>(null);

  const { items, total, loading, error, reload, pagination } = usePagedList<Role, Record<string, never>>(
    ({ limit, offset }) => fetchRoles(offset, Math.min(limit, PAGE_SIZE)),
    {},
    { pageSize: PAGE_SIZE },
  );

  async function handleDelete(role: Role) {
    try {
      await deleteRole(role.id);
      message.success('角色已删除。');
      reload();
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '删除角色失败');
      }
    }
  }

  const columns: ColumnsType<Role> = [
    {
      title: '角色名',
      dataIndex: 'name',
      key: 'name',
      width: 180,
      render: (value: string, record) => (
        <span className="font-mono text-zinc-900">
          {value}
          {record.is_system ? (
            <Tag color="blue" className="ml-1.5">
              内置
            </Tag>
          ) : null}
        </span>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (value: string | null) => value ?? '-',
    },
    {
      title: '权限数',
      key: 'permissions',
      width: 100,
      render: (_, record) => (
        <Tooltip title={record.permissions.map(permissionKeyLabel).join('、')}>
          <span className="text-zinc-500">{record.permissions.length}</span>
        </Tooltip>
      ),
    },
    {
      title: '系统角色',
      dataIndex: 'is_system',
      key: 'is_system',
      width: 100,
      render: (value: boolean) => (value ? '是' : '否'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 130,
      render: (_, record) => (
        <Space>
          <Button
            type="link"
            size="small"
            title={record.is_system ? '内置角色可编辑但不可删除' : `编辑 ${record.name}`}
            onClick={() => setDialog({ mode: 'edit', data: record })}
          >
            编辑
          </Button>
          <Popconfirm
            title={`确定删除角色「${record.name}」？`}
            description="该操作不可撤销。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            disabled={record.is_system}
            onConfirm={() => void handleDelete(record)}
          >
            <Button
              type="link"
              size="small"
              danger
              disabled={record.is_system}
              title={record.is_system ? '系统内置角色不可删除' : undefined}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="角色管理"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setDialog({ mode: 'create' })}>
          新建角色
        </Button>
      }
      styles={{ body: { paddingTop: 0 } }}
    >
      {error ? <Alert type="error" showIcon message={error} className="mb-3" /> : null}

      <Table<Role>
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={pagination}
        locale={{ emptyText: '暂无角色 · 创建角色后即可批量分配权限给用户' }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">共 {total} 个角色</div>

      <RoleDialogForm
        open={dialog !== null}
        dialog={dialog}
        onCancel={() => setDialog(null)}
        onSubmit={async (form) => {
          try {
            if (dialog?.mode === 'edit') {
              await updateRole(dialog.data.id, {
                name: form.name.trim() || undefined,
                description: form.description.trim() || undefined,
                permissions: form.permissions,
              });
              message.success('角色已更新。');
            } else {
              await createRole({
                name: form.name.trim(),
                description: form.description.trim() || undefined,
                permissions: form.permissions,
              });
              message.success('角色已创建。');
            }
            setDialog(null);
            reload();
          } catch (caught) {
            if (!isAuthRequiredError(caught)) {
              message.error(caught instanceof Error ? caught.message : '保存角色失败');
            }
            throw caught;
          }
        }}
      />
    </Card>
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

  useEffect(() => {
    if (open) {
      setForm(dialog?.mode === 'edit' ? toForm(dialog.data) : emptyForm());
      setErrorMessage(null);
      setSubmitting(false);
    }
  }, [open, dialog]);

  const canSubmit = !submitting && form.name.trim().length > 0;

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
      title={isEditMode ? `编辑角色 · ${dialog?.data.name}` : '新建角色'}
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
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="role-name">
              角色名
            </label>
            <Input
              id="role-name"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="例如：reviewer"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="role-description">
              描述（选填）
            </label>
            <Input
              id="role-description"
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
              placeholder="选填"
            />
          </div>
        </div>

        <PermissionGroup
          title="菜单权限"
          permissions={MENU_PERMISSIONS}
          value={form.permissions}
          onChange={(permissions) => setForm((prev) => ({ ...prev, permissions }))}
        />
        <PermissionGroup
          title="操作权限"
          permissions={ACTION_PERMISSIONS}
          value={form.permissions}
          onChange={(permissions) => setForm((prev) => ({ ...prev, permissions }))}
        />

        {errorMessage ? <Alert type="error" showIcon message={errorMessage} /> : null}
      </div>
    </Modal>
  );
}

interface PermissionGroupProps {
  title: string;
  permissions: string[];
  value: string[];
  onChange: (permissions: string[]) => void;
}

/** 权限分组 UI：一组 antd Checkbox。 */
function PermissionGroup({ title, permissions, value, onChange }: PermissionGroupProps) {
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] font-medium text-zinc-500">{title}</div>
      <Checkbox.Group
        className="grid grid-cols-3 gap-x-3 gap-y-1.5 rounded-md border border-zinc-200 p-2.5"
        value={value}
        onChange={(values) => onChange(values as string[])}
        options={permissions.map((permission) => ({
          value: permission,
          label: PERMISSION_LABELS[permission] ?? permission,
        }))}
      />
    </div>
  );
}
