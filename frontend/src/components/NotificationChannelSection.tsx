/**
 * 项目钉钉推送配置区。
 *
 * 作为 ProjectDialog 编辑模式下的独立子区块，直接调用通知渠道 CRUD API。
 * 通知渠道是项目的子资源（需先有 project_id 才能创建），因此仅在编辑
 * 模式下展示；新增项目模式由父组件提示"保存后可配置推送"。
 *
 * 后端在每次 Review 完成后按启用渠道推送汇总消息（标题 + MR 标题 +
 * Review ID + 问题/阻断数 + 详情链接），不推送逐条 finding。
 */

import { useCallback, useEffect, useState } from 'react';

import {
  NotificationChannel,
  NotificationChannelFormPayload,
  createNotificationChannel,
  deleteNotificationChannel,
  fetchNotificationChannels,
  updateNotificationChannel,
} from '../api';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { PasswordInput } from './ui/password-input';

interface NotificationChannelSectionProps {
  projectId: string;
}

interface NewChannelForm {
  name: string;
  webhook_url: string;
  secret: string;
  enabled: boolean;
}

const EMPTY_FORM: NewChannelForm = {
  name: '',
  webhook_url: '',
  secret: '',
  enabled: true,
};

export function NotificationChannelSection({
  projectId,
}: NotificationChannelSectionProps) {
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<NewChannelForm>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const loadChannels = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await fetchNotificationChannels(projectId);
      setChannels(list);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '加载推送配置失败');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void loadChannels();
  }, [loadChannels]);

  async function handleToggleEnabled(channel: NotificationChannel) {
    const nextEnabled = !channel.enabled;
    // 乐观更新：先切 UI，失败回滚。
    setChannels((prev) =>
      prev.map((c) => (c.id === channel.id ? { ...c, enabled: nextEnabled } : c)),
    );
    try {
      await updateNotificationChannel(projectId, channel.id, {
        enabled: nextEnabled,
      });
    } catch (caught) {
      // 回滚
      setChannels((prev) =>
        prev.map((c) =>
          c.id === channel.id ? { ...c, enabled: channel.enabled } : c,
        ),
      );
      setError(caught instanceof Error ? caught.message : '切换状态失败');
    }
  }

  async function handleDelete(channel: NotificationChannel) {
    if (!window.confirm(`确定删除推送渠道「${channel.name}」？`)) {
      return;
    }
    setChannels((prev) => prev.filter((c) => c.id !== channel.id));
    try {
      await deleteNotificationChannel(projectId, channel.id);
    } catch (caught) {
      // 回滚
      void loadChannels();
      setError(caught instanceof Error ? caught.message : '删除失败');
    }
  }

  async function handleAddChannel() {
    if (!form.name.trim() || !form.webhook_url.trim()) {
      setFormError('渠道名称和 Webhook 地址不能为空');
      return;
    }
    setSubmitting(true);
    setFormError(null);
    const payload: NotificationChannelFormPayload = {
      channel_type: 'dingtalk',
      name: form.name.trim(),
      webhook_url: form.webhook_url.trim(),
      secret: form.secret.trim() || null,
      enabled: form.enabled,
    };
    try {
      const created = await createNotificationChannel(projectId, payload);
      setChannels((prev) => [...prev, created]);
      setForm(EMPTY_FORM);
      setShowForm(false);
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : '添加失败');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-3">
      {/* 渠道列表 */}
      {loading ? (
        <p className="text-[12px] text-zinc-400">加载推送配置…</p>
      ) : channels.length === 0 ? (
        <p className="text-[12px] text-zinc-400">
          暂未配置推送渠道，该项目审查结果不会推送到钉钉。
        </p>
      ) : (
        <div className="space-y-2">
          {channels.map((channel) => (
            <div
              key={channel.id}
              className="flex items-center justify-between rounded-md border border-zinc-200 bg-white px-3 py-2"
            >
              <div className="flex items-center gap-2.5">
                <span className="text-[11px] rounded bg-indigo-50 px-1.5 py-0.5 font-medium text-indigo-600">
                  钉钉
                </span>
                <span className="text-[13px] font-medium text-zinc-700">
                  {channel.name}
                </span>
                <span className="text-[11px] text-zinc-400">
                  {channel.webhook_url}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => void handleToggleEnabled(channel)}
                  className={`text-[11px] font-medium ${
                    channel.enabled ? 'text-emerald-600' : 'text-zinc-400'
                  }`}
                >
                  {channel.enabled ? '● 已启用' : '○ 已停用'}
                </button>
                <button
                  type="button"
                  onClick={() => void handleDelete(channel)}
                  className="text-[11px] font-medium text-rose-500 hover:text-rose-600"
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 新增表单 */}
      {showForm ? (
        <div className="space-y-3 rounded-md border border-dashed border-zinc-300 bg-zinc-50/50 p-3">
          <div className="space-y-1.5">
            <Label htmlFor="channel-name">渠道名称</Label>
            <Input
              id="channel-name"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="例如：前端组机器人"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="channel-webhook">钉钉 Webhook 地址</Label>
            <PasswordInput
              id="channel-webhook"
              value={form.webhook_url}
              onChange={(event) =>
                setForm({ ...form, webhook_url: event.target.value })
              }
              placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
              toggleAriaLabel="切换 Webhook 地址显示"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="channel-secret">加签密钥（可选）</Label>
            <PasswordInput
              id="channel-secret"
              value={form.secret}
              onChange={(event) => setForm({ ...form, secret: event.target.value })}
              placeholder="开启「加签」安全设置时填写"
              toggleAriaLabel="切换密钥显示"
            />
          </div>
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 text-[12px] text-zinc-600">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) =>
                  setForm({ ...form, enabled: event.target.checked })
                }
                className="size-4 rounded border-zinc-300 accent-indigo-600 focus:ring-indigo-500"
              />
              创建后立即启用
            </label>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={submitting}
                onClick={() => {
                  setShowForm(false);
                  setForm(EMPTY_FORM);
                  setFormError(null);
                }}
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={submitting}
                onClick={() => void handleAddChannel()}
              >
                {submitting ? '添加中…' : '添加渠道'}
              </Button>
            </div>
          </div>
          {formError ? (
            <p className="text-[12px] text-rose-600">{formError}</p>
          ) : null}
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className="text-[12px] font-medium text-indigo-600 hover:text-indigo-700"
        >
          + 添加钉钉推送渠道
        </button>
      )}

      {error ? (
        <p className="text-[12px] text-rose-600">{error}</p>
      ) : null}
    </div>
  );
}
