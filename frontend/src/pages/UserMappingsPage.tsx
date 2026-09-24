/**
 * 「用户映射」页（antd 版）：管理各项目 GitLab 用户名 -> 钉钉手机号 / UserID
 * 的映射（钉钉通知 @MR 创建人功能的配置入口）。
 *
 * 项目下拉本页自取（fetchProjectsAll）；映射列表为项目子资源、后端全量
 * 返回，用 Table 客户端分页展示（数据完整，非假分页）。
 */

import { useEffect, useState } from 'react';
import { PlusOutlined } from '@ant-design/icons';
import { App as AntApp, Alert, Button, Card, Empty, Input, Modal, Popconfirm, Select, Space, Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  createUserMapping,
  deleteUserMapping,
  fetchProjectsAll,
  fetchUserMappings,
  isAuthRequiredError,
  updateUserMapping,
  type ProjectConfig,
  type UserMapping,
} from '../api';

type MappingDialog = { mode: 'create' } | { mode: 'edit'; data: UserMapping } | null;

type MappingFormState = {
  gitlab_username: string;
  dingtalk_mobile: string;
  dingtalk_userid: string;
  display_name: string;
};

const emptyMappingForm: MappingFormState = {
  gitlab_username: '',
  dingtalk_mobile: '',
  dingtalk_userid: '',
  display_name: '',
};

export function UserMappingsPage() {
  const { message } = AntApp.useApp();
  const [projects, setProjects] = useState<ProjectConfig[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [mappings, setMappings] = useState<UserMapping[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<MappingDialog>(null);

  useEffect(() => {
    let active = true;
    fetchProjectsAll()
      .then((page) => {
        if (active) setProjects(page.items);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  // 默认选中第一个项目；选中项目被删掉后回落到第一个。
  useEffect(() => {
    if (projects.length === 0) {
      setSelectedProjectId('');
      return;
    }
    if (!projects.some((project) => project.id === selectedProjectId)) {
      setSelectedProjectId(projects[0].id);
    }
  }, [projects, selectedProjectId]);

  // 切换项目后拉取该项目下的映射列表。
  useEffect(() => {
    if (!selectedProjectId) {
      setMappings([]);
      return;
    }
    let active = true;
    setLoading(true);
    setError(null);
    fetchUserMappings(selectedProjectId)
      .then((items) => {
        if (active) setMappings(items);
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : '加载用户映射失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedProjectId]);

  async function handleDelete(mapping: UserMapping) {
    try {
      await deleteUserMapping(mapping.id);
      message.success('映射已删除。');
      setMappings((prev) => prev.filter((item) => item.id !== mapping.id));
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '删除用户映射失败');
      }
    }
  }

  const selectedProject = projects.find((project) => project.id === selectedProjectId) ?? null;

  const columns: ColumnsType<UserMapping> = [
    {
      title: 'GitLab 用户名',
      dataIndex: 'gitlab_username',
      key: 'gitlab_username',
      width: 180,
      render: (value: string) => <span className="font-mono text-zinc-900">{value}</span>,
    },
    {
      title: '钉钉手机号',
      dataIndex: 'dingtalk_mobile',
      key: 'dingtalk_mobile',
      width: 160,
      render: (value: string) => <span className="font-mono text-zinc-700">{value}</span>,
    },
    {
      title: '钉钉 UserID',
      dataIndex: 'dingtalk_userid',
      key: 'dingtalk_userid',
      ellipsis: true,
      render: (value: string | null) => (
        <span className="font-mono text-zinc-500">{value ?? '-'}</span>
      ),
    },
    {
      title: '显示名称',
      dataIndex: 'display_name',
      key: 'display_name',
      ellipsis: true,
      render: (value: string | null) => value ?? '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => setDialog({ mode: 'edit', data: record })}>
            编辑
          </Button>
          <Popconfirm
            title={`确定删除「${record.gitlab_username}」的映射？`}
            description="该操作不可撤销。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => void handleDelete(record)}
          >
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="用户映射"
      extra={
        <Space>
          <Select
            aria-label="选择项目"
            placeholder="选择项目"
            style={{ minWidth: 200 }}
            value={selectedProjectId || undefined}
            disabled={projects.length === 0}
            options={projects.map((project) => ({ value: project.id, label: project.name }))}
            onChange={(value) => setSelectedProjectId(value ?? '')}
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={!selectedProjectId}
            onClick={() => setDialog({ mode: 'create' })}
          >
            添加映射
          </Button>
        </Space>
      }
      styles={{ body: { paddingTop: 0 } }}
    >
      <div className="py-2 text-[12px] text-zinc-500">
        {selectedProject
          ? `${selectedProject.name} · ${mappings.length} 条映射 · 钉钉通知 @MR 创建人依赖此配置`
          : 'GitLab 用户名与钉钉手机号的映射关系'}
      </div>

      {error ? <Alert type="error" showIcon message={error} className="mb-3" /> : null}

      <Table<UserMapping>
        rowKey="id"
        columns={columns}
        dataSource={mappings}
        loading={loading}
        pagination={{ pageSize: 20, showTotal: (total) => `共 ${total} 条` }}
        locale={{
          emptyText: projects.length === 0 ? (
            <Empty description="暂无 GitLab 项目 · 请先在「GitLab 项目」页添加项目，再配置用户映射" />
          ) : (
            <Empty description="暂无用户映射 · 添加映射后，钉钉通知会 @MR 创建人" />
          ),
        }}
      />

      <MappingDialogForm
        open={dialog !== null}
        dialog={dialog}
        onCancel={() => setDialog(null)}
        onSubmit={async (form) => {
          // 选填字段留空时传 null，保持后端「未填」语义。
          const payload = {
            gitlab_username: form.gitlab_username.trim(),
            dingtalk_mobile: form.dingtalk_mobile.trim(),
            dingtalk_userid: form.dingtalk_userid.trim() || null,
            display_name: form.display_name.trim() || null,
          };
          try {
            if (dialog?.mode === 'edit') {
              await updateUserMapping(dialog.data.id, payload);
              message.success('映射已更新。');
            } else if (selectedProjectId) {
              await createUserMapping(selectedProjectId, payload);
              message.success('映射已添加。');
            }
            setDialog(null);
            if (selectedProjectId) {
              const items = await fetchUserMappings(selectedProjectId);
              setMappings(items);
            }
          } catch (caught) {
            if (!isAuthRequiredError(caught)) {
              message.error(caught instanceof Error ? caught.message : '保存用户映射失败');
            }
            throw caught;
          }
        }}
      />
    </Card>
  );
}

interface MappingDialogFormProps {
  open: boolean;
  dialog: MappingDialog;
  onCancel: () => void;
  onSubmit: (form: MappingFormState) => Promise<void>;
}

/** 新增 / 编辑映射弹窗（纯受控组件，接口调用交给父组件）。 */
function MappingDialogForm({ open, dialog, onCancel, onSubmit }: MappingDialogFormProps) {
  const [form, setForm] = useState<MappingFormState>(emptyMappingForm);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const isEditMode = dialog?.mode === 'edit';

  useEffect(() => {
    if (open) {
      setForm(
        dialog?.mode === 'edit'
          ? {
              gitlab_username: dialog.data.gitlab_username,
              dingtalk_mobile: dialog.data.dingtalk_mobile,
              dingtalk_userid: dialog.data.dingtalk_userid ?? '',
              display_name: dialog.data.display_name ?? '',
            }
          : emptyMappingForm,
      );
      setErrorMessage(null);
      setSubmitting(false);
    }
  }, [open, dialog]);

  const canSubmit =
    !submitting && form.gitlab_username.trim().length > 0 && form.dingtalk_mobile.trim().length > 0;

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
      title={isEditMode ? '编辑用户映射' : '添加用户映射'}
      okText="保存"
      cancelText="取消"
      okButtonProps={{ disabled: !canSubmit, loading: submitting }}
      cancelButtonProps={{ disabled: submitting }}
      onOk={() => void handleSubmit()}
      maskClosable={false}
      destroyOnHidden
    >
      <div className="space-y-3 pt-2">
        <div className="space-y-1.5">
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="mapping-gitlab-username">
            GitLab 用户名
          </label>
          <Input
            id="mapping-gitlab-username"
            value={form.gitlab_username}
            onChange={(event) => setForm({ ...form, gitlab_username: event.target.value })}
            placeholder="例如：alice"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="mapping-dingtalk-mobile">
            钉钉手机号
          </label>
          <Input
            id="mapping-dingtalk-mobile"
            value={form.dingtalk_mobile}
            onChange={(event) => setForm({ ...form, dingtalk_mobile: event.target.value })}
            placeholder="例如：13800000000"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="mapping-dingtalk-userid">
              钉钉 UserID（选填）
            </label>
            <Input
              id="mapping-dingtalk-userid"
              value={form.dingtalk_userid}
              onChange={(event) => setForm({ ...form, dingtalk_userid: event.target.value })}
              placeholder="选填"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="mapping-display-name">
              显示名称（选填）
            </label>
            <Input
              id="mapping-display-name"
              value={form.display_name}
              onChange={(event) => setForm({ ...form, display_name: event.target.value })}
              placeholder="选填"
            />
          </div>
        </div>

        {errorMessage ? <Alert type="error" showIcon message={errorMessage} /> : null}
      </div>
    </Modal>
  );
}
