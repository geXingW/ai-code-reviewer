/**
 * 新增/编辑 GitLab 项目弹窗。
 *
 * 支持两种模式：
 * - 新增模式：initialData = null
 * - 编辑模式：initialData = ProjectConfig
 *
 * 纯受控组件，不做接口调用，onSubmit 由父组件把 payload 交给后端。
 */

import { useEffect, useState } from 'react';

import { ProjectConfig, ProjectFormPayload, RuleConfig, ProjectRuleFormPayload } from '../../api';
import { Button } from '../ui/button';
import { Dialog } from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { PasswordInput } from '../ui/password-input';
import { Select } from '../ui/select';
import { RuleSelector } from '../RuleSelector';
import { NotificationChannelSection } from '../NotificationChannelSection';

const SEVERITY_OPTIONS = ['INFO', 'WARNING', 'BLOCKER'] as const;

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
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
          {title}
        </h3>
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
        // 编辑模式：回填数据
        setForm({
          name: initialData.name,
          gitlab_project_id: initialData.gitlab_project_id,
          gitlab_base_url: initialData.gitlab_base_url,
          // 编辑模式不反显密钥（后端返回脱敏值 "****"），置空让用户按需重填。
          // 留空提交时由 handleSubmit 跳过，后端也不会覆盖数据库中的密钥。
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
        // 新增模式：空白表单
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
      // 编辑模式下，密钥字段留空表示"不修改"——从 payload 中移除，
      // JSON.stringify 会自动忽略 deleted 属性，后端 exclude_unset 也不会更新。
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
    <Dialog
      open={open}
      onClose={submitting ? () => {} : onCancel}
      title={isEditMode ? '编辑 GitLab 项目' : '新增 GitLab 项目'}
      subtitle={isEditMode ? initialData.name : '接入项目后可通过 Webhook 自动触发 MR 审查'}
      maxWidthClass="max-w-2xl"
      footer={
        <>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={submitting}
            onClick={onCancel}
          >
            取消
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!canSubmit}
            onClick={() => void handleSubmit()}
          >
            {submitting ? '保存中…' : '保存'}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {/* ───────── 基础信息 ───────── */}
        <FieldGroup title="基础信息">
          <div className="space-y-1.5">
            <Label htmlFor="project-name">项目名称</Label>
            <Input
              id="project-name"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="例如：my-project"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="project-gitlab-id">GitLab Project ID</Label>
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
            <Label htmlFor="project-gitlab-base-url">GitLab Base URL</Label>
            <Input
              id="project-gitlab-base-url"
              value={form.gitlab_base_url}
              onChange={(event) => setForm({ ...form, gitlab_base_url: event.target.value })}
              placeholder="https://gitlab.example.com"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="project-token">GitLab Access Token</Label>
              <PasswordInput
                id="project-token"
                value={form.gitlab_access_token}
                onChange={(event) => setForm({ ...form, gitlab_access_token: event.target.value })}
                placeholder={isEditMode ? '为空则不修改' : 'glpat-...'}
                toggleAriaLabel="切换 Access Token 显示"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="project-secret">Webhook Secret</Label>
              <PasswordInput
                id="project-secret"
                value={form.webhook_secret}
                onChange={(event) => setForm({ ...form, webhook_secret: event.target.value })}
                placeholder={isEditMode ? '为空则不修改' : '随机字符串'}
                toggleAriaLabel="切换 Webhook Secret 显示"
              />
            </div>
          </div>
        </FieldGroup>

        {/* ───────── 审查配置 ───────── */}
        <FieldGroup title="审查配置">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="project-engine">默认审查引擎</Label>
              <Select
                id="project-engine"
                value={form.engine_id}
                onChange={(event) => setForm({ ...form, engine_id: event.target.value })}
              >
                {engineOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="project-provider">AI 供应商</Label>
              <Select
                id="project-provider"
                value={form.provider_id}
                onChange={(event) => setForm({ ...form, provider_id: event.target.value })}
              >
                {providerOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="project-timeout">超时秒数</Label>
              <Input
                id="project-timeout"
                type="number"
                value={String(form.timeout_seconds)}
                onChange={(event) => setForm({ ...form, timeout_seconds: Number(event.target.value) || 0 })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="project-max-files">最大文件数</Label>
              <Input
                id="project-max-files"
                type="number"
                value={String(form.max_files)}
                onChange={(event) => setForm({ ...form, max_files: Number(event.target.value) || 0 })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="project-severity">默认阻断级别</Label>
              <Select
                id="project-severity"
                value={form.default_block_severity}
                onChange={(event) => setForm({ ...form, default_block_severity: event.target.value as ProjectFormPayload['default_block_severity'] })}
              >
                {SEVERITY_OPTIONS.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </Select>
            </div>
          </div>

          {/* Commit 推送审查：复用「启用项目」的卡片风格 */}
          <div className="flex items-center justify-between rounded-lg border border-zinc-200 bg-zinc-50/50 px-3 py-2.5">
            <div className="flex items-center gap-2.5">
              <input
                type="checkbox"
                id="project-commit-review-enabled"
                checked={form.commit_review_enabled}
                onChange={(event) =>
                  setForm({ ...form, commit_review_enabled: event.target.checked })
                }
                className="size-4 rounded border-zinc-300 accent-indigo-600 focus:ring-indigo-500"
              />
              <Label htmlFor="project-commit-review-enabled" className="text-[13px] text-zinc-700">
                启用 commit 推送审查
              </Label>
            </div>
            <div className="flex items-center gap-2">
              <Label
                htmlFor="project-commit-review-max"
                className={`text-[11px] ${form.commit_review_enabled ? 'text-zinc-500' : 'text-zinc-300'}`}
              >
                单次推送最多审查 commit 数 (1-20)
              </Label>
              <Input
                id="project-commit-review-max"
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
                className={`w-20 text-right ${form.commit_review_enabled ? '' : 'opacity-50'}`}
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
          <div className="flex items-center gap-2.5">
            <input
              type="checkbox"
              id="project-enabled"
              checked={form.enabled}
              onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
              className="size-4 rounded border-zinc-300 accent-indigo-600 focus:ring-indigo-500"
            />
            <Label htmlFor="project-enabled" className="text-[13px] text-zinc-700">
              启用项目
            </Label>
          </div>
          <span className={`text-[11px] font-medium ${form.enabled ? 'text-emerald-600' : 'text-zinc-400'}`}>
            {form.enabled ? '● 已启用' : '○ 已停用'}
          </span>
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
