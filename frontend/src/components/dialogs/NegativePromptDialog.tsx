/**
 * 项目级负样本提示词弹窗。
 *
 * 自包含组件：自己管理数据加载与提交，不走 App.tsx 全局 state。
 * 打开时拉取该项目当前提示词 + 已批准负样本数量；「生成」调 LLM 生成
 * 结果填入 textarea（不自动保存），用户编辑后手动「保存」。
 */

import { useEffect, useState } from 'react';

import {
  fetchProjectNegativePrompt,
  generateProjectNegativePrompt,
  updateProjectNegativePrompt,
} from '../../api';
import { Button } from '../ui/button';
import { Dialog } from '../ui/dialog';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';

export interface NegativePromptDialogProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
  /** Dialog subtitle 用。 */
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
          setErrorMessage(caught instanceof Error ? caught.message : '加载失败');
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
      setErrorMessage(caught instanceof Error ? caught.message : '生成失败');
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
      setErrorMessage(caught instanceof Error ? caught.message : '保存失败');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={loading || saving || generating ? () => {} : onClose}
      title="负样本提示词"
      subtitle={`${projectName} · 仅作用于该项目的 MR 审查`}
      maxWidthClass="max-w-2xl"
      footer={
        <>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={loading || saving || generating}
            onClick={onClose}
          >
            关闭
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={loading || generating || saving || exampleCount === 0}
            title={exampleCount === 0 ? '该项目负样本库为空' : undefined}
            onClick={() => void handleGenerate()}
          >
            {generating ? '生成中...' : '生成'}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={loading || saving || generating}
            onClick={() => void handleSave()}
          >
            {saving ? '保存中...' : '保存'}
          </Button>
        </>
      }
    >
      {loading ? (
        <div className="py-10 text-center text-[13px] text-zinc-400">加载中...</div>
      ) : (
        <>
          <div className="space-y-1.5">
            <Label htmlFor="negative-prompt-textarea">提示词内容</Label>
            <Textarea
              id="negative-prompt-textarea"
              value={content}
              onChange={(event) => {
                setContent(event.target.value);
                setSaved(false);
              }}
              placeholder="点击「生成」根据该项目负样本库自动生成，也可以手动编辑..."
              className="min-h-[240px] font-mono text-sm leading-relaxed"
            />
            <span className="block text-[11px] text-zinc-500">
              为空时回退结构化负样本注入。最长 50000 字符，修改后约 60 秒内生效（带缓存）。
            </span>
          </div>
          <div className="flex items-center gap-3 text-[11px] text-zinc-500">
            {exampleCount === 0 ? <span>该项目负样本库为空，无法生成</span> : null}
            {sourceCount > 0 ? <span>基于 {sourceCount} 条负样本生成</span> : null}
            {saved ? <span className="text-emerald-600">已保存</span> : null}
          </div>
          {errorMessage ? (
            <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-700" role="alert">
              {errorMessage}
            </div>
          ) : null}
        </>
      )}
    </Dialog>
  );
}
