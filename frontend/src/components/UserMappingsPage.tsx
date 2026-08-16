/**
 * 「用户映射」独立页：管理各项目 GitLab 用户名 -> 钉钉手机号 / UserID 的映射
 * （钉钉通知 @MR 创建人功能的配置入口）。
 *
 * 页面结构：顶部项目选择器 + 右上角「+ 添加映射」按钮 + 映射列表表格。
 * 新增 / 编辑走同一个弹窗，CRUD 逻辑全部封装在本组件内，App 只透传项目列表。
 */

import { useEffect, useState } from 'react';
import { Users } from 'lucide-react';

import {
  ProjectConfig,
  UserMapping,
  UserMappingCreatePayload,
  createUserMapping,
  deleteUserMapping,
  fetchUserMappings,
  updateUserMapping,
} from '../api';
import { Button } from './ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Dialog } from './ui/dialog';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Select } from './ui/select';
import { EmptyState } from './EmptyState';

export interface UserMappingsPageProps {
  /** 从 App 透传已加载的项目列表（复用 fetchProjects 的数据）。 */
  projects: ProjectConfig[];
}

type UserMappingDialog =
  | { mode: 'create' }
  | { mode: 'edit'; data: UserMapping }
  | null;

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

export function UserMappingsPage({ projects }: UserMappingsPageProps) {
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [mappings, setMappings] = useState<UserMapping[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<UserMappingDialog>(null);

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
        if (active) {
          setMappings(items);
        }
      })
      .catch((caught) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : '加载用户映射失败');
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
  }, [selectedProjectId]);

  async function handleDelete(mapping: UserMapping) {
    if (!window.confirm(`确定删除「${mapping.gitlab_username}」的映射？该操作不可撤销。`)) {
      return;
    }
    setError(null);
    try {
      await deleteUserMapping(mapping.id);
      setMappings((prev) => prev.filter((item) => item.id !== mapping.id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '删除用户映射失败');
    }
  }

  async function handleSubmit(form: MappingFormState) {
    // 选填字段留空时传 null，保持后端"未填"语义。
    const payload: UserMappingCreatePayload = {
      gitlab_username: form.gitlab_username.trim(),
      dingtalk_mobile: form.dingtalk_mobile.trim(),
      dingtalk_userid: form.dingtalk_userid.trim() || null,
      display_name: form.display_name.trim() || null,
    };
    setError(null);
    try {
      if (dialog?.mode === 'edit') {
        const updated = await updateUserMapping(dialog.data.id, payload);
        setMappings((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      } else if (selectedProjectId) {
        const created = await createUserMapping(selectedProjectId, payload);
        setMappings((prev) => [...prev, created]);
      }
      setDialog(null);
    } catch (caught) {
      // 抛给弹窗展示行内错误，同时父页保留 banner。
      setError(caught instanceof Error ? caught.message : '保存用户映射失败');
      throw caught;
    }
  }

  const selectedProject = projects.find((project) => project.id === selectedProjectId) ?? null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>用户映射</CardTitle>
            <CardDescription>
              {selectedProject
                ? `${selectedProject.name} · ${mappings.length} 条映射 · 钉钉通知 @MR 创建人依赖此配置`
                : 'GitLab 用户名与钉钉手机号的映射关系'}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Select
              aria-label="选择项目"
              value={selectedProjectId}
              onChange={(event) => setSelectedProjectId(event.target.value)}
              className="w-56"
              disabled={projects.length === 0}
            >
              {projects.map((project) => (
                <option key={project.id} value={project.id}>{project.name}</option>
              ))}
            </Select>
            <Button size="sm" disabled={!selectedProjectId} onClick={() => setDialog({ mode: 'create' })}>
              + 添加映射
            </Button>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {error ? (
            <div className="m-4 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
              {error}
            </div>
          ) : null}
          {projects.length === 0 ? (
            <EmptyState icon={Users} title="暂无 GitLab 项目" description="请先在「GitLab 项目」页添加项目，再配置用户映射" />
          ) : loading ? (
            <div className="p-6 text-center text-[13px] text-zinc-500">加载中…</div>
          ) : mappings.length === 0 ? (
            <EmptyState
              icon={Users}
              title="暂无用户映射"
              description="添加映射后，钉钉通知会 @MR 创建人"
              action={(
                <Button size="sm" disabled={!selectedProjectId} onClick={() => setDialog({ mode: 'create' })}>
                  + 添加映射
                </Button>
              )}
            />
          ) : (
            <div className="divide-y divide-zinc-100">
              <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_120px] gap-2 px-4 py-2 text-[10px] font-medium uppercase text-zinc-400">
                <div>GitLab 用户名</div>
                <div>钉钉手机号</div>
                <div>钉钉 UserID</div>
                <div>显示名称</div>
                <div className="text-right">操作</div>
              </div>
              {mappings.map((mapping) => (
                <div
                  key={mapping.id}
                  className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_120px] items-center gap-2 px-4 py-2.5 text-[13px] hover:bg-zinc-50 transition-colors"
                >
                  <div className="truncate font-mono text-zinc-900">{mapping.gitlab_username}</div>
                  <div className="truncate font-mono text-zinc-700">{mapping.dingtalk_mobile}</div>
                  <div className="truncate font-mono text-zinc-500">{mapping.dingtalk_userid ?? '-'}</div>
                  <div className="truncate text-zinc-700">{mapping.display_name ?? '-'}</div>
                  <div className="flex items-center justify-end gap-2">
                    <Button variant="ghost" size="sm" type="button" onClick={() => setDialog({ mode: 'edit', data: mapping })}>
                      编辑
                    </Button>
                    <Button variant="destructive" size="sm" type="button" onClick={() => void handleDelete(mapping)}>
                      删除
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <UserMappingDialogForm
        open={dialog !== null}
        dialog={dialog}
        onCancel={() => setDialog(null)}
        onSubmit={handleSubmit}
      />
    </div>
  );
}

interface UserMappingDialogFormProps {
  open: boolean;
  dialog: UserMappingDialog;
  onCancel: () => void;
  onSubmit: (form: MappingFormState) => Promise<void>;
}

/** 新增 / 编辑映射弹窗（纯受控组件，接口调用交给父组件）。 */
function UserMappingDialogForm({ open, dialog, onCancel, onSubmit }: UserMappingDialogFormProps) {
  const [form, setForm] = useState<MappingFormState>(emptyMappingForm);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const isEditMode = dialog?.mode === 'edit';

  // dialog 每次重新打开都重置表单（编辑模式回填数据）。
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
    // dialog 引用变化即视为重新打开，依赖里带上它。
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
    <Dialog
      open={open}
      onClose={submitting ? () => {} : onCancel}
      title={isEditMode ? '编辑用户映射' : '添加用户映射'}
      subtitle={isEditMode ? dialog?.data.gitlab_username : 'GitLab 用户名与钉钉手机号的映射，用于钉钉通知 @MR 创建人'}
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
        <div className="space-y-1.5">
          <Label htmlFor="user-mapping-gitlab-username">GitLab 用户名</Label>
          <Input
            id="user-mapping-gitlab-username"
            value={form.gitlab_username}
            onChange={(event) => setForm({ ...form, gitlab_username: event.target.value })}
            placeholder="例如：alice"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="user-mapping-dingtalk-mobile">钉钉手机号</Label>
          <Input
            id="user-mapping-dingtalk-mobile"
            value={form.dingtalk_mobile}
            onChange={(event) => setForm({ ...form, dingtalk_mobile: event.target.value })}
            placeholder="例如：13800000000"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="user-mapping-dingtalk-userid">钉钉 UserID（选填）</Label>
            <Input
              id="user-mapping-dingtalk-userid"
              value={form.dingtalk_userid}
              onChange={(event) => setForm({ ...form, dingtalk_userid: event.target.value })}
              placeholder="选填"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="user-mapping-display-name">显示名称（选填）</Label>
            <Input
              id="user-mapping-display-name"
              value={form.display_name}
              onChange={(event) => setForm({ ...form, display_name: event.target.value })}
              placeholder="选填"
            />
          </div>
        </div>
      </div>

      {errorMessage ? (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-700" role="alert">
          {errorMessage}
        </div>
      ) : null}
    </Dialog>
  );
}
