/**
 * 新增/编辑 GitLab 项目弹窗（antd 版）。
 *
 * 支持两种模式：新增（initialData = null）/ 编辑（initialData = ProjectConfig）。
 * 纯受控组件，不做接口调用，onSubmit 由父组件把 payload 交给后端。
 * 含规则勾选面板（RuleSelector）与钉钉推送配置区（NotificationChannelSection）。
 */

import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Input, Modal, Select, Switch } from 'antd';

import type { ProjectConfig, ProjectFormPayload, ProjectRuleFormPayload, RuleConfig } from '../../api';
import { RuleSelector } from '../RuleSelector';
import { NotificationChannelSection } from '../NotificationChannelSection';

const SEVERITY_OPTIONS = [
  { value: 'INFO', label: 'INFO' },
  { value: 'WARNING', label: 'WARNING' },
  { value: 'BLOCKER', label: 'BLOCKER' },
];

export interface ProjectDialogProps {
  open: boolean;
  /** null = 新增模式，ProjectConfig = 编辑模式 */
  initialData: ProjectConfig | null;
  engineOptions: Array<{ value: string; label: string }>;
  providerOptions: Array<{ value: string; label: string }>;
  rules: RuleConfig[];
  onCancel: () => void;
  onSubmit: (payload: ProjectFormPayload) => Promise<void>;
}

const initialEmptyForm: ProjectFormPayload = {
  name: '',
  gitlab_project_id: '',
  gitlab_base_url: '',
  gitlab_access_token: '',
  webhook_secret: '',
  engine_id: '',
  provider_id: '',
  enabled: true,
  timeout_seconds: 300,
  max_files: 50,
  commit_review_enabled: false,
  commit_review_max_per_push: 10,
  default_block_severity: 'BLOCKER',
  rules: [],
};

function toggleRuleSelection(
  rules: ProjectRuleFormPayload[],
  ruleId: string,
  enabled: boolean,
): ProjectRuleFormPayload[] {
  if (enabled) {
    if (rules.some((r) => r.rule_id === ruleId)) {
      return rules;
    }
    return [...rules, { rule_id: ruleId, enabled: true }];
  }
  return rules.filter((r) => r.rule_id !== ruleId);
}

/** 字段分组小标题：统一视觉节奏。 */
function FieldGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 pt-1">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">{title}</h3>
        <div className="h-px flex-1 bg-zinc-100" />
      </div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

export function ProjectDialog({
  open,
  initialData,
  engineOptions,
  providerOptions,
  rules,
  onCancel,
  onSubmit,
}: ProjectDialogProps) {
  const [form, setForm] = useState<ProjectFormPayload>(initialEmptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // dialog 每次重新打开都重置表单
  useEffect(() => {
    if (open) {
      if (initialData) {
        // 编辑模式：回填数据。密钥不反显（后端返回脱敏值 "****"），置空表示不修改。
        setForm({
          name: initialData.name,
          gitlab_project_id: initialData.gitlab_project_id,
          gitlab_base_url: initialData.gitlab_base_url,
          gitlab_access_token: '',
          webhook_secret: '',
          engine_id: initialData.engine_id || '',
          provider_id: initialData.provider_id || '',
          enabled: initialData.enabled,
          timeout_seconds: initialData.timeout_seconds,
          max_files: initialData.max_files,
          commit_review_enabled: initialData.commit_review_enabled,
          commit_review_max_per_push: initialData.commit_review_max_per_push,
          default_block_severity: initialData.default_block_severity as ProjectFormPayload['default_block_severity'],
          rules: initialData.rules.map((r) => ({ rule_id: r.rule_id, enabled: r.enabled })),
        });
      } else {
        setForm(initialEmptyForm);
      }
      setErrorMessage(null);
      setSubmitting(false);
    }
  }, [open, initialData]);

  const isEditMode = initialData !== null;
  const canSubmit = !submitting && form.name.trim() && form.gitlab_project_id.trim();

  async function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const payload: Partial<ProjectFormPayload> = { ...form };
      // 编辑模式下，密钥字段留空表示「不修改」——从 payload 中移除，
      // 后端 exclude_unset 不会更新该字段。
      if (isEditMode) {
        if (!payload.gitlab_access_token?.trim()) {
          delete payload.gitlab_access_token;
        }
        if (!payload.webhook_secret?.trim()) {
          delete payload.webhook_secret;
        }
      }
      await onSubmit(payload as ProjectFormPayload);
    } catch (caught) {
      setErrorMessage(caught instanceof Error ? caught.message : '提交失败');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onCancel={submitting ? undefined : onCancel}
      title={isEditMode ? '编辑 GitLab 项目' : '新增 GitLab 项目'}
      okText="保存"
      cancelText="取消"
      okButtonProps={{ disabled: !canSubmit, loading: submitting }}
      cancelButtonProps={{ disabled: submitting }}
      onOk={() => void handleSubmit()}
      maskClosable={false}
      destroyOnHidden
      width={720}
    >
      <div className="space-y-5 pt-2">
        {/* ───────── 基础信息 ───────── */}
        <FieldGroup title="基础信息">
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="project-name">
              项目名称
            </label>
            <Input
              id="project-name"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="例如：my-project"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="project-gitlab-id">
              GitLab Project ID
            </label>
            <Input
              id="project-gitlab-id"
              value={form.gitlab_project_id}
              onChange={(event) => setForm({ ...form, gitlab_project_id: event.target.value })}
              placeholder="123456"
            />
          </div>
        </FieldGroup>

        {/* ───────── GitLab 连接 ───────── */}
        <FieldGroup title="GitLab 连接">
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="project-gitlab-base-url">
              GitLab Base URL
            </label>
            <Input
              id="project-gitlab-base-url"
              value={form.gitlab_base_url}
              onChange={(event) => setForm({ ...form, gitlab_base_url: event.target.value })}
              placeholder="https://gitlab.example.com"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-zinc-600" htmlFor="project-token">
                GitLab Access Token
              </label>
              <Input.Password
                id="project-token"
                value={form.gitlab_access_token}
                onChange={(event) => setForm({ ...form, gitlab_access_token: event.target.value })}
                placeholder={isEditMode ? '为空则不修改' : 'glpat-...'}
                autoComplete="new-password"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-zinc-600" htmlFor="project-secret">
                Webhook Secret
              </label>
              <Input.Password
                id="project-secret"
                value={form.webhook_secret}
                onChange={(event) => setForm({ ...form, webhook_secret: event.target.value })}
                placeholder={isEditMode ? '为空则不修改' : '随机字符串'}
                autoComplete="new-password"
              />
            </div>
          </div>
        </FieldGroup>

        {/* ───────── 审查配置 ───────── */}
        <FieldGroup title="审查配置">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-zinc-600" htmlFor="project-engine">
                默认审查引擎
              </label>
              <Select
                id="project-engine"
                value={form.engine_id || ''}
                options={engineOptions}
                style={{ width: '100%' }}
                onChange={(value) => setForm({ ...form, engine_id: value })}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-zinc-600" htmlFor="project-provider">
                AI 供应商
              </label>
              <Select
                id="project-provider"
                value={form.provider_id || ''}
                options={providerOptions}
                style={{ width: '100%' }}
                onChange={(value) => setForm({ ...form, provider_id: value })}
              />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-zinc-600" htmlFor="project-timeout">
                超时秒数
              </label>
              <Input
                id="project-timeout"
                type="number"
                value={String(form.timeout_seconds)}
                onChange={(event) =>
                  setForm({ ...form, timeout_seconds: Number(event.target.value) || 0 })
                }
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-zinc-600" htmlFor="project-max-files">
                最大文件数
              </label>
              <Input
                id="project-max-files"
                type="number"
                value={String(form.max_files)}
                onChange={(event) => setForm({ ...form, max_files: Number(event.target.value) || 0 })}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-zinc-600" htmlFor="project-severity">
                默认阻断级别
              </label>
              <Select
                id="project-severity"
                value={form.default_block_severity}
                options={SEVERITY_OPTIONS}
                style={{ width: '100%' }}
                onChange={(value) =>
                  setForm({ ...form, default_block_severity: value as ProjectFormPayload['default_block_severity'] })
                }
              />
            </div>
          </div>

          <div className="flex items-center justify-between rounded-lg border border-zinc-200 bg-zinc-50/50 px-3 py-2.5">
            <Checkbox
              checked={form.commit_review_enabled}
              onChange={(event) => setForm({ ...form, commit_review_enabled: event.target.checked })}
            >
              启用 commit 推送审查
            </Checkbox>
            <div className="flex items-center gap-2">
              <span
                className="text-[11px] text-zinc-400"
                title="合并审查后该字段不再截断 commit，仅作兼容保留"
              >
                单次推送最多审查 commit 数（兼容保留）
              </span>
              <Input
                type="number"
                min={1}
                max={20}
                disabled={!form.commit_review_enabled}
                value={String(form.commit_review_max_per_push)}
                onChange={(event) => {
                  const parsed = Number(event.target.value) || 0;
                  setForm({
                    ...form,
                    commit_review_max_per_push: Math.min(20, Math.max(1, parsed)),
                  });
                }}
                style={{ width: 80, textAlign: 'right' }}
              />
            </div>
          </div>
        </FieldGroup>

        {/* ───────── 审查规则 ───────── */}
        <FieldGroup title="审查规则">
          <RuleSelector
            rules={rules}
            selectedRuleIds={form.rules.map((r) => r.rule_id)}
            onToggle={(ruleId, enabled) =>
              setForm((prev) => ({
                ...prev,
                rules: toggleRuleSelection(prev.rules, ruleId, enabled),
              }))
            }
            onBulkReplace={(ruleIds) =>
              setForm((prev) => ({
                ...prev,
                rules: ruleIds.map((id) => ({ rule_id: id, enabled: true })),
              }))
            }
          />
        </FieldGroup>

        {/* ───────── 钉钉推送配置 ───────── */}
        <FieldGroup title="钉钉推送">
          {isEditMode && initialData ? (
            <NotificationChannelSection projectId={initialData.id} />
          ) : (
            <p className="text-[12px] text-zinc-400">
              保存项目后可在此配置钉钉群机器人 Webhook，审查完成后自动推送汇总信息到钉钉群。
            </p>
          )}
        </FieldGroup>

        {/* ───────── 启用项目 ───────── */}
        <div className="flex items-center justify-between rounded-lg border border-zinc-200 bg-zinc-50/50 px-3 py-2.5">
          <span className="text-[13px] text-zinc-700">启用项目</span>
          <Switch
            checked={form.enabled}
            onChange={(checked) => setForm({ ...form, enabled: checked })}
            aria-label="启用项目"
          />
        </div>

        {errorMessage ? <Alert type="error" showIcon message={errorMessage} /> : null}
      </div>
    </Modal>
  );
}
