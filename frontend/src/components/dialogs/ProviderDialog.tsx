/**
 * 新增/编辑模型供应商弹窗（antd 版）。
 *
 * 支持两种模式：新增（initialData = null）/ 编辑（initialData = ProviderConfig）。
 * 纯受控组件，不做接口调用，onSubmit 由父组件把 payload 交给后端。
 */

import { useEffect, useState } from 'react';
import { Button, Input, Modal, Select, Switch } from 'antd';
import { Alert } from 'antd';

import type { ProviderConfig, ProviderFormPayload } from '../../api';

const PROTOCOL_OPTIONS = [
  { value: 'openai_compatible', label: 'openai_compatible' },
  { value: 'anthropic', label: 'anthropic' },
  { value: 'custom', label: 'custom' },
];

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

export function ProviderDialog({ open, initialData, onCancel, onSubmit }: ProviderDialogProps) {
  const [form, setForm] = useState<ProviderFormPayload>(initialEmptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // dialog 每次重新打开都重置表单
  useEffect(() => {
    if (open) {
      if (initialData) {
        // 编辑模式：回填数据。密钥不反显（后端返回脱敏值 "****"），置空表示不修改。
        setForm({
          name: initialData.name,
          protocol: initialData.protocol as ProviderFormPayload['protocol'],
          base_url: initialData.base_url,
          api_key: '',
          model: initialData.model,
          temperature: initialData.temperature,
          max_tokens: initialData.max_tokens,
          enabled: initialData.enabled,
        });
      } else {
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
      // 编辑模式下，密钥字段留空表示「不修改」——从 payload 中移除，
      // 后端 exclude_unset 不会更新该字段。
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
    <Modal
      open={open}
      onCancel={submitting ? undefined : onCancel}
      title={isEditMode ? '编辑供应商' : '新增供应商'}
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
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="provider-name">
            名称
          </label>
          <Input
            id="provider-name"
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="例如：OpenAI GPT-4"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="provider-protocol">
            协议
          </label>
          <Select
            id="provider-protocol"
            value={form.protocol}
            options={PROTOCOL_OPTIONS}
            style={{ width: '100%' }}
            onChange={(value) => setForm({ ...form, protocol: value as ProviderFormPayload['protocol'] })}
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="provider-base-url">
            Base URL
          </label>
          <Input
            id="provider-base-url"
            value={form.base_url}
            onChange={(event) => setForm({ ...form, base_url: event.target.value })}
            placeholder="https://api.openai.com/v1"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-[12px] font-medium text-zinc-600" htmlFor="provider-api-key">
            API Key
          </label>
          <Input.Password
            id="provider-api-key"
            value={form.api_key}
            onChange={(event) => setForm({ ...form, api_key: event.target.value })}
            placeholder={isEditMode ? '为空则不修改' : 'sk-...'}
            autoComplete="new-password"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="provider-model">
              模型
            </label>
            <Input
              id="provider-model"
              value={form.model}
              onChange={(event) => setForm({ ...form, model: event.target.value })}
              placeholder="gpt-4"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-zinc-600" htmlFor="provider-max-tokens">
              Max Tokens
            </label>
            <Input
              id="provider-max-tokens"
              type="number"
              value={String(form.max_tokens)}
              onChange={(event) => setForm({ ...form, max_tokens: Number(event.target.value) || 0 })}
            />
          </div>
        </div>

        <div className="flex items-center justify-between rounded-lg border border-zinc-200 bg-zinc-50/50 px-3 py-2.5">
          <span className="text-[13px] text-zinc-700">启用供应商</span>
          <Switch
            checked={form.enabled}
            onChange={(checked) => setForm({ ...form, enabled: checked })}
            aria-label="启用供应商"
          />
        </div>

        {errorMessage ? <Alert type="error" showIcon message={errorMessage} /> : null}
      </div>
    </Modal>
  );
}
