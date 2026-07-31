/**
 * 新增/编辑审查规则弹窗。
 *
 * 支持两种模式：
 * - 新增模式：initialData = null
 * - 编辑模式：initialData = RuleConfig
 *
 * 纯受控组件，不做接口调用，onSubmit 由父组件把 payload 交给后端。
 */

import { useEffect, useState } from 'react';

import { RuleConfig, RuleFormPayload } from '../../api';
import { Button } from '../ui/button';
import { Dialog } from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';

const SEVERITY_OPTIONS = ['INFO', 'WARNING', 'BLOCKER'] as const;

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

export function RuleDialog({
  open,
  initialData,
  onCancel,
  onSubmit,
}: RuleDialogProps) {
  const [form, setForm] = useState<RuleFormPayload>(initialEmptyForm);
  // 标签输入框的当前文本；回车或点「添加标签」时并入 form.tags。
  const [tagInput, setTagInput] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // dialog 每次重新打开都重置表单
  useEffect(() => {
    if (open) {
      if (initialData) {
        // 编辑模式：回填数据
        setForm({
          rule_id: initialData.rule_id,
          title: initialData.title,
          prompt_snippet: initialData.prompt_snippet,
          severity_default: initialData.severity_default as RuleFormPayload['severity_default'],
          tags: [...initialData.tags],
          enabled: initialData.enabled,
        });
      } else {
        // 新增模式：空白表单
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

  /** 删除一个已添加的标签。 */
  function removeTag(tag: string) {
    setForm((prev) => ({ ...prev, tags: prev.tags.filter((t) => t !== tag) }));
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
    <Dialog
      open={open}
      onClose={submitting ? () => {} : onCancel}
      title={isEditMode ? '编辑审查规则' : '新增审查规则'}
      subtitle={isEditMode ? initialData.title : '定义 AI 审查依据的规则模板'}
      maxWidthClass="max-w-xl"
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
      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="rule-id">规则 ID <span className="text-zinc-400 text-[11px]">（可选：留空则自动从标题生成）</span></Label>
          <Input
            id="rule-id"
            value={form.rule_id}
            onChange={(event) => setForm({ ...form, rule_id: event.target.value })}
            placeholder="例如：no-hardcoded-secrets"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="rule-title">标题</Label>
          <Input
            id="rule-title"
            value={form.title}
            onChange={(event) => setForm({ ...form, title: event.target.value })}
            placeholder="例如：检测硬编码密钥"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="rule-prompt">提示片段</Label>
          <Textarea
            id="rule-prompt"
            rows={4}
            value={form.prompt_snippet}
            onChange={(event) => setForm({ ...form, prompt_snippet: event.target.value })}
            placeholder="描述这个规则的 AI 提示词片段..."
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="rule-severity">默认严重级别</Label>
          <select
            id="rule-severity"
            value={form.severity_default}
            onChange={(event) => setForm({ ...form, severity_default: event.target.value as RuleFormPayload['severity_default'] })}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
          >
            {SEVERITY_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="rule-tag-input">标签 <span className="text-zinc-400 text-[11px]">（按标签筛选规则，可多个）</span></Label>
          <div className="flex items-center gap-2">
            <Input
              id="rule-tag-input"
              value={tagInput}
              onChange={(event) => setTagInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  // 回车也添加标签，并阻止表单默认提交行为。
                  event.preventDefault();
                  addTag();
                }
              }}
              placeholder="例如：security"
            />
            <Button type="button" variant="secondary" size="sm" onClick={() => addTag()}>
              添加标签
            </Button>
          </div>
          {form.tags.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {form.tags.map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 text-[12px] font-medium"
                >
                  {tag}
                  <button
                    type="button"
                    onClick={() => removeTag(tag)}
                    aria-label={`删除标签 ${tag}`}
                    className="text-indigo-400 hover:text-indigo-700 leading-none"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="rule-enabled"
            checked={form.enabled}
            onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
            className="h-4 w-4 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500"
          />
          <Label htmlFor="rule-enabled">启用规则</Label>
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
