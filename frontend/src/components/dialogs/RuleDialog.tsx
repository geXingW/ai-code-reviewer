/**
 * 新增/编辑审查规则弹窗（antd 版）。
 *
 * 支持两种模式：新增（initialData = null）/ 编辑（initialData = RuleConfig）。
 * 纯受控组件，不做接口调用，onSubmit 由父组件把 payload 交给后端。
 */

import { useEffect, useState } from 'react';
import { Alert, Button, Input, Modal, Select, Space, Switch, Tag } from 'antd';

import type { RuleConfig, RuleFormPayload } from '../../api';

const SEVERITY_OPTIONS = [
  { value: 'INFO', label: 'INFO' },
  { value: 'WARNING', label: 'WARNING' },
  { value: 'BLOCKER', label: 'BLOCKER' },
];

export interface RuleDialogProps {
  open: boolean;
  /** null = 新增模式，RuleConfig = 编辑模式 */
  initialData: RuleConfig | null;
  onCancel: () => void;
  onSubmit: (payload: RuleFormPayload) => Promise<void>;
}

const initialEmptyForm: RuleFormPayload = {
  rule_id: '',
  title: '',
  prompt_snippet: '',
  severity_default: 'WARNING',
  tags: [],
  enabled: true,
};

export function RuleDialog({ open, initialData, onCancel, onSubmit }: RuleDialogProps) {
  const [form, setForm] = useState<RuleFormPayload>(initialEmptyForm);
  // 标签输入框的当前文本；回车或点「添加标签」时并入 form.tags。
  const [tagInput, setTagInput] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // dialog 每次重新打开都重置表单
  useEffect(() => {
    if (open) {
      if (initialData) {
        setForm({
          rule_id: initialData.rule_id,
          title: initialData.title,
          prompt_snippet: initialData.prompt_snippet,
          severity_default: initialData.severity_default as RuleFormPayload['severity_default'],
          tags: [...initialData.tags],
          enabled: initialData.enabled,
        });
      } else {
        setForm(initialEmptyForm);
      }
      setTagInput('');
      setErrorMessage(null);
      setSubmitting(false);
    }
  }, [open, initialData]);

  const isEditMode = initialData !== null;
  const canSubmit = !submitting && form.title.trim() && form.prompt_snippet.trim();

  /** 把输入框里的文本并入 tags（去重、忽略空白）。 */
  function addTag() {
    const value = tagInput.trim();
    if (!value) {
      return;
    }
    if (form.tags.includes(value)) {
      setTagInput('');
      return;
    }
    setForm((prev) => ({ ...prev, tags: [...prev.tags, value] }));
    setTagInput('');
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
      setErrorMessage(caught instanceof Error ? caught.message : '提交失败');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onCancel={submitting ? undefined : onCancel}
      title={isEditMode ? '编辑审查规则' : '新增审查规则'}
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
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="rule-id">
            规则 ID <span className="font-normal text-zinc-400">（可选：留空则自动从标题生成）</span>
          </label>
          <Input
            id="rule-id"
            value={form.rule_id}
            disabled={isEditMode}
            onChange={(event) => setForm({ ...form, rule_id: event.target.value })}
            placeholder="例如：no-hardcoded-secrets"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="rule-title">
            标题
          </label>
          <Input
            id="rule-title"
            value={form.title}
            onChange={(event) => setForm({ ...form, title: event.target.value })}
            placeholder="例如：检测硬编码密钥"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="rule-prompt">
            提示片段
          </label>
          <Input.TextArea
            id="rule-prompt"
            rows={8}
            value={form.prompt_snippet}
            onChange={(event) => setForm({ ...form, prompt_snippet: event.target.value })}
            placeholder="描述这个规则的 AI 提示词片段..."
            style={{ fontFamily: 'JetBrains Mono, monospace' }}
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="rule-severity">
            默认严重级别
          </label>
          <Select
            id="rule-severity"
            value={form.severity_default}
            options={SEVERITY_OPTIONS}
            style={{ width: '100%' }}
            onChange={(value) => setForm({ ...form, severity_default: value as RuleFormPayload['severity_default'] })}
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="rule-tag-input">
            标签 <span className="font-normal text-zinc-400">（按标签筛选规则，可多个）</span>
          </label>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              id="rule-tag-input"
              value={tagInput}
              onChange={(event) => setTagInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  // 回车也添加标签，并阻止默认提交行为。
                  event.preventDefault();
                  addTag();
                }
              }}
              placeholder="例如：security"
            />
            <Button onClick={() => addTag()}>添加标签</Button>
          </Space.Compact>
          {form.tags.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {form.tags.map((tag) => (
                <Tag
                  key={tag}
                  color="blue"
                  closable
                  onClose={() => setForm((prev) => ({ ...prev, tags: prev.tags.filter((t) => t !== tag) }))}
                >
                  {tag}
                </Tag>
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-between rounded-lg border border-zinc-200 bg-zinc-50/50 px-3 py-2.5">
          <span className="text-[13px] text-zinc-700">启用规则</span>
          <Switch
            checked={form.enabled}
            onChange={(checked) => setForm({ ...form, enabled: checked })}
            aria-label="启用规则"
          />
        </div>

        {errorMessage ? <Alert type="error" showIcon message={errorMessage} /> : null}
      </div>
    </Modal>
  );
}
