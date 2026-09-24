/**
 * 「全局提示词」页：编辑并保存注入每次 LLM 审查 system prompt 开头的全局提示词。
 */

import { useEffect, useState } from 'react';
import { App as AntApp, Alert, Button, Card, Input } from 'antd';

import { fetchGlobalPrompt, isAuthRequiredError, updateGlobalPrompt } from '../api';

export function GlobalPromptPage() {
  const { message } = AntApp.useApp();
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchGlobalPrompt()
      .then((result) => {
        if (active) setContent(result.content);
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : '加载失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updateGlobalPrompt(content);
      message.success('全局提示词已保存。');
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '保存失败');
      }
      setError(caught instanceof Error ? caught.message : '保存失败');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title="全局提示词">
      <Alert
        className="mb-4"
        type="info"
        showIcon
        message="这段提示词会被注入到每次 LLM 代码审查的 system prompt 开头，用于定义全局审查原则和风格。为空时不注入；修改后约 60 秒内生效（带缓存）。"
      />
      {error ? <Alert type="error" showIcon message={error} className="mb-3" /> : null}
      <Input.TextArea
        aria-label="提示词内容"
        value={content}
        onChange={(event) => setContent(event.target.value)}
        placeholder="例如：请特别关注安全性问题，优先审查输入验证、权限控制、SQL 注入等..."
        rows={14}
        style={{ fontFamily: 'JetBrains Mono, monospace' }}
        maxLength={50000}
        showCount
      />
      <div className="mt-3">
        <Button type="primary" loading={saving} onClick={() => void handleSave()}>
          保存
        </Button>
      </div>
    </Card>
  );
}
