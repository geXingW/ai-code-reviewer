/**
 * 「标记已解决」弹窗。
 *
 * 与「标记误报」流程类似，但不走误报审核流程，不进负样本库，
 * 仅把 finding.status 设为 resolved 并触发 MR 阻断状态重算。
 *
 * 适用于：
 * - 问题已在后续 commit 中修复
 * - 团队评估后认为可接受（技术债务）
 * - AI 报告的上下文问题但不是系统性误报
 *
 * 组件是纯受控：不做接口调用，onSubmit 由父组件把 payload 交给后端。
 */

import { useEffect, useState } from 'react';

import { FindingRecord } from '../../api';
import { Button } from '../ui/button';
import { Dialog } from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';
import {
  categoryDisplay,
  severityDisplay,
} from '../../lib/findingTaxonomy';

export interface ResolveDialogProps {
  open: boolean;
  finding: FindingRecord | null;
  defaultResolvedBy: string;
  onCancel: () => void;
  onSubmit: (payload: { resolved_by: string; reason: string }) => Promise<void>;
}

const MIN_REASON_LEN = 5;
const MAX_RESOLVED_BY_LEN = 255;

export function ResolveDialog({
  open,
  finding,
  defaultResolvedBy,
  onCancel,
  onSubmit,
}: ResolveDialogProps) {
  const [resolvedBy, setResolvedBy] = useState(defaultResolvedBy);
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // dialog 每次重新打开都重置表单：避免上一次的 reason 残留。
  useEffect(() => {
    if (open) {
      setResolvedBy(defaultResolvedBy);
      setReason('');
      setErrorMessage(null);
      setSubmitting(false);
    }
  }, [open, defaultResolvedBy]);

  const trimmedResolvedBy = resolvedBy.trim();
  const trimmedReason = reason.trim();
  // 前端校验：resolved_by 必填且长度合规；reason 必填且 >= 5 字符——避免无意义原因。
  const resolvedByValid =
    trimmedResolvedBy.length >= 1 && trimmedResolvedBy.length <= MAX_RESOLVED_BY_LEN;
  const reasonValid = trimmedReason.length >= MIN_REASON_LEN;
  const canSubmit = resolvedByValid && reasonValid && !submitting;

  async function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    try {
      await onSubmit({ resolved_by: trimmedResolvedBy, reason: trimmedReason });
    } catch (caught) {
      // 提交失败时保留弹窗，允许用户改文案重试；错误映射跟外部 handleCaughtError 相同语义。
      setErrorMessage(caught instanceof Error ? caught.message : '提交失败');
    } finally {
      setSubmitting(false);
    }
  }

  const sev = finding ? severityDisplay(finding.severity) : null;
  const cat = finding ? categoryDisplay(finding.category) : null;

  return (
    <Dialog
      open={open}
      onClose={submitting ? () => {} : onCancel}
      title="标记为已解决"
      subtitle={finding?.title ?? undefined}
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
            {submitting ? '提交中…' : '确认解决'}
          </Button>
        </>
      }
    >
      {/* 上下文只读区：让运营者标记前对齐 finding 具体位置。 */}
      {finding ? (
        <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3 text-[12px] text-zinc-700 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            {sev ? (
              <span className="inline-flex items-center gap-1">
                <span aria-hidden>{sev.emoji}</span>
                <span className="font-medium">{sev.label}</span>
              </span>
            ) : null}
            <span aria-hidden>{cat?.emoji}</span>
            <span className="text-zinc-500">{cat?.label}</span>
          </div>
          <div className="font-mono text-[11px] text-zinc-600">
            {finding.file_path}:{finding.line_number ?? '-'} · {finding.rule_id}
          </div>
          <div className="text-[11px] text-zinc-500">
            {finding.project_name ?? '未知项目'}
            {finding.mr_iid ? ` · MR !${finding.mr_iid}` : ''}
          </div>
          {finding.description || finding.suggestion ? (
            <details className="mt-1">
              <summary className="cursor-pointer text-[11px] text-zinc-500">
                展开描述 / 建议
              </summary>
              {finding.description ? (
                <div className="mt-1 whitespace-pre-wrap text-[12px] text-zinc-700">
                  {finding.description}
                </div>
              ) : null}
              {finding.suggestion ? (
                <div className="mt-1 whitespace-pre-wrap text-[12px] text-zinc-600">
                  <span className="font-medium">建议：</span>
                  {finding.suggestion}
                </div>
              ) : null}
            </details>
          ) : null}
        </div>
      ) : null}

      <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 p-2 text-[12px] text-emerald-700">
        <p className="font-medium">标记已解决后：</p>
        <ul className="mt-1 list-disc list-inside space-y-0.5 text-[11px]">
          <li>该问题不再计入 MR 阻断状态</li>
          <li>GitLab MR 上的该条讨论将被标记为已解决</li>
          <li>不会进入误报审核流程，不会进入负样本库</li>
        </ul>
      </div>

      <div className="space-y-1.5 mt-4">
        <Label htmlFor="resolve-marked-by">标记人</Label>
        <Input
          id="resolve-marked-by"
          value={resolvedBy}
          maxLength={MAX_RESOLVED_BY_LEN}
          onChange={(event) => setResolvedBy(event.target.value)}
        />
        {!resolvedByValid ? (
          <div className="text-[11px] text-rose-600">
            标记人不能为空，长度需在 1–{MAX_RESOLVED_BY_LEN} 字符之间。
          </div>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="resolve-reason">原因说明</Label>
        <Textarea
          id="resolve-reason"
          rows={4}
          value={reason}
          placeholder="请描述解决原因，例如：已在 commit a1b2c3d 中修复、团队评估后认为可接受、上下文问题非系统性误报"
          onChange={(event) => setReason(event.target.value)}
        />
        {!reasonValid ? (
          <div className="text-[11px] text-rose-600">
            原因至少 {MIN_REASON_LEN} 字符——请具体说明解决的方式或评估的理由。
          </div>
        ) : null}
      </div>

      {errorMessage ? (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-700" role="alert">
          {errorMessage}
        </div>
      ) : null}
    </Dialog>
  );
}
