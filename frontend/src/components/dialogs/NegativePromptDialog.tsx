/**
 * 项目级负样本提示词弹窗（antd 版）。
 *
 * 自包含组件：自己管理数据加载与提交。打开时拉取该项目当前提示词 +
 * 已批准负样本数量；「生成」调 LLM 生成结果填入 textarea（不自动保存），
 * 用户编辑后手动「保存」。
 */

import { useEffect, useState } from 'react';
import { Alert, Button, Input, Modal, Spin } from 'antd';

import {
  fetchProjectNegativePrompt,
  generateProjectNegativePrompt,
  isAuthRequiredError,
  updateProjectNegativePrompt,
} from '../../api';

export interface NegativePromptDialogProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
  /** Dialog 副标题用。 */
  projectName: string;
}

export function NegativePromptDialog({
  open,
  onClose,
  projectId,
  projectName,
}: NegativePromptDialogProps) {
  const [loading, setLoading] = useState(true);
  const [content, setContent] = useState('');
  const [exampleCount, setExampleCount] = useState(0);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [sourceCount, setSourceCount] = useState(0);
  const [saved, setSaved] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // 每次打开都重新拉取该项目的当前配置
  useEffect(() => {
    if (!open) {
      return;
    }
    let active = true;
    setLoading(true);
    setErrorMessage(null);
    setSaved(false);
    setSourceCount(0);
    fetchProjectNegativePrompt(projectId)
      .then((result) => {
        if (!active) {
          return;
        }
        setContent(result.content);
        setExampleCount(result.example_count);
      })
      .catch((caught) => {
        if (active) {
          if (!isAuthRequiredError(caught)) {
            setErrorMessage(caught instanceof Error ? caught.message : '加载失败');
          }
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
  }, [open, projectId]);

  async function handleGenerate() {
    if (generating || exampleCount === 0) {
      return;
    }
    setGenerating(true);
    setErrorMessage(null);
    setSaved(false);
    try {
      const result = await generateProjectNegativePrompt(projectId);
      setContent(result.content);
      setSourceCount(result.source_count);
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        setErrorMessage(caught instanceof Error ? caught.message : '生成失败');
      }
    } finally {
      setGenerating(false);
    }
  }

  async function handleSave() {
    if (saving) {
      return;
    }
    setSaving(true);
    setErrorMessage(null);
    try {
      await updateProjectNegativePrompt(projectId, content);
      setSaved(true);
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        setErrorMessage(caught instanceof Error ? caught.message : '保存失败');
      }
    } finally {
      setSaving(false);
    }
  }

  const busy = loading || saving || generating;

  return (
    <Modal
      open={open}
      onCancel={busy ? undefined : onClose}
      title="负样本提示词"
      width={720}
      footer={[
        <Button key="close" disabled={busy} onClick={onClose}>
          关闭
        </Button>,
        <Button
          key="generate"
          disabled={busy || exampleCount === 0}
          title={exampleCount === 0 ? '该项目负样本库为空' : undefined}
          onClick={() => void handleGenerate()}
        >
          {generating ? '生成中...' : '生成'}
        </Button>,
        <Button key="save" type="primary" loading={saving} disabled={loading || generating} onClick={() => void handleSave()}>
          保存
        </Button>,
      ]}
      maskClosable={false}
      destroyOnHidden
    >
      <div className="pt-2">
        <div className="mb-1 text-[12px] text-zinc-500">
          {projectName} · 仅作用于该项目的 MR 审查
        </div>
        {loading ? (
          <div className="flex justify-center py-10">
            <Spin />
          </div>
        ) : (
          <>
            <Input.TextArea
              aria-label="提示词内容"
              value={content}
              onChange={(event) => {
                setContent(event.target.value);
                setSaved(false);
              }}
              placeholder="点击「生成」根据该项目负样本库自动生成，也可以手动编辑..."
              rows={12}
              style={{ fontFamily: 'JetBrains Mono, monospace' }}
              maxLength={50000}
            />
            <div className="mt-2 flex items-center gap-3 text-[11px] text-zinc-500">
              <span>为空时回退结构化负样本注入。最长 50000 字符，修改后约 60 秒内生效（带缓存）。</span>
              {exampleCount === 0 ? <span className="shrink-0">该项目负样本库为空，无法生成</span> : null}
              {sourceCount > 0 ? <span className="shrink-0">基于 {sourceCount} 条负样本生成</span> : null}
              {saved ? <span className="shrink-0 text-emerald-600">已保存</span> : null}
            </div>
            {errorMessage ? <Alert type="error" showIcon message={errorMessage} className="mt-2" /> : null}
          </>
        )}
      </div>
    </Modal>
  );
}
