/**
 * 新增/编辑模型供应商弹窗。
 *
 * 支持两种模式：
 * - 新增模式：initialData = null
 * - 编辑模式：initialData = ProviderConfig
 *
 * 纯受控组件，不做接口调用，onSubmit 由父组件把 payload 交给后端。
 */

import { useEffect, useState } from 'react';

import { ProviderConfig, ProviderFormPayload } from '../../api';
import { Button } from '../ui/button';
import { Dialog } from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { PasswordInput } from '../ui/password-input';
import { Select } from '../ui/select';

const PROTOCOL_OPTIONS = ['openai_compatible', 'anthropic', 'custom'] as const;

export interface ProviderDialogProps {
  open: boolean;
  /** null = 新增模式，ProviderConfig = 编辑模式 */
  initialData: ProviderConfig | null;
  onCancel: () => void;
  onSubmit: (payload: ProviderFormPayload) => Promise<void>;
}

const initialEmptyForm: ProviderFormPayload = {
  name: '',
  protocol: 'openai_compatible',
  base_url: '',
  api_key: '',
  model: '',
  temperature: 0,
  max_tokens: 4096,
  enabled: true,
};

export function ProviderDialog({
  open,
  initialData,
  onCancel,
  onSubmit,
}: ProviderDialogProps) {
  const [form, setForm] = useState<ProviderFormPayload>(initialEmptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // dialog 每次重新打开都重置表单
  useEffect(() => {
    if (open) {
      if (initialData) {
        // 编辑模式：回填数据
        setForm({
          name: initialData.name,
          protocol: initialData.protocol as ProviderFormPayload['protocol'],
          base_url: initialData.base_url,
          // 编辑模式不反显密钥（后端返回脱敏值 "****"），置空让用户按需重填。
          // 留空提交时由 handleSubmit 跳过，后端也不会覆盖数据库中的密钥。
          api_key: '',
          model: initialData.model,
          temperature: initialData.temperature,
          max_tokens: initialData.max_tokens,
          enabled: initialData.enabled,
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
  const canSubmit = !submitting && form.name.trim() && form.base_url.trim() && form.model.trim();

  async function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const payload: Partial<ProviderFormPayload> = { ...form };
      // 编辑模式下，密钥字段留空表示"不修改"——从 payload 中移除，
      // JSON.stringify 会自动忽略 deleted 属性，后端 exclude_unset 也不会更新。
      if (isEditMode && !payload.api_key?.trim()) {
        delete payload.api_key;
      }
      await onSubmit(payload as ProviderFormPayload);
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
      title={isEditMode ? '编辑供应商' : '新增供应商'}
      subtitle={isEditMode ? initialData.name : '目前支持 OpenAI 兼容、Anthropic、Custom 协议'}
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
          <Label htmlFor="provider-name">名称</Label>
          <Input
            id="provider-name"
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="例如：OpenAI GPT-4"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="provider-protocol">协议</Label>
          <Select
            id="provider-protocol"
            value={form.protocol}
            onChange={(event) => setForm({ ...form, protocol: event.target.value as ProviderFormPayload['protocol'] })}
          >
            {PROTOCOL_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="provider-base-url">Base URL</Label>
          <Input
            id="provider-base-url"
            value={form.base_url}
            onChange={(event) => setForm({ ...form, base_url: event.target.value })}
            placeholder="https://api.openai.com/v1"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="provider-api-key">API Key</Label>
          <PasswordInput
            id="provider-api-key"
            value={form.api_key}
            onChange={(event) => setForm({ ...form, api_key: event.target.value })}
            placeholder={isEditMode ? '为空则不修改' : 'sk-...'}
            toggleAriaLabel="切换 API Key 显示"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="provider-model">模型</Label>
            <Input
              id="provider-model"
              value={form.model}
              onChange={(event) => setForm({ ...form, model: event.target.value })}
              placeholder="gpt-4"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="provider-max-tokens">Max Tokens</Label>
            <Input
              id="provider-max-tokens"
              type="number"
              value={String(form.max_tokens)}
              onChange={(event) => setForm({ ...form, max_tokens: Number(event.target.value) || 0 })}
            />
          </div>
        </div>

        <div className="flex items-center justify-between rounded-lg border border-zinc-200 bg-zinc-50/50 px-3 py-2.5">
          <div className="flex items-center gap-2.5">
            <input
              type="checkbox"
              id="provider-enabled"
              checked={form.enabled}
              onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
              className="size-4 rounded border-zinc-300 accent-indigo-600 focus:ring-indigo-500"
            />
            <Label htmlFor="provider-enabled" className="text-[13px] text-zinc-700">
              启用供应商
            </Label>
          </div>
          <span className={`text-[11px] font-medium ${form.enabled ? 'text-emerald-600' : 'text-zinc-400'}`}>
            {form.enabled ? '● 已启用' : '○ 已停用'}
          </span>
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
