/**
 * 「标记为误报」弹窗。
 *
 * 之前的旧流程：点问题条目右侧「标记误报」按钮 → 直接调 API，operator 走
 * 页面级 state（默认 admin@example.com），reason 空就写死"MVP 管理台标记"。
 * 这个弹窗把"标记人 / 原因"变成必填的表单动作：
 *   - marked_by 默认取 sessionStorage 里的登录用户名，允许编辑；
 *   - reason 必填、最少 5 字符，防止运营者一路回车留下无意义的"MVP 管理台标记"。
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

export interface MarkFalsePositiveDialogProps {
  open: boolean;
  finding: FindingRecord | null;
  defaultMarkedBy: string;
  onCancel: () => void;
  onSubmit: (payload: { marked_by: string; reason: string }) => Promise<void>;
}

const MIN_REASON_LEN = 5;
const MAX_MARKED_BY_LEN = 255;

export function MarkFalsePositiveDialog({
  open,
  finding,
  defaultMarkedBy,
  onCancel,
  onSubmit,
}: MarkFalsePositiveDialogProps) {
  const [markedBy, setMarkedBy] = useState(defaultMarkedBy);
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // dialog 每次重新打开都重置表单：避免上一次的 reason 残留。
  useEffect(() => {
    if (open) {
      setMarkedBy(defaultMarkedBy);
      setReason('');
      setErrorMessage(null);
      setSubmitting(false);
    }
  }, [open, defaultMarkedBy]);

  const trimmedMarkedBy = markedBy.trim();
  const trimmedReason = reason.trim();
  // 前端校验：marked_by 必填且长度合规；reason 必填且 >= 5 字符——避免无意义原因。
  const markedByValid =
    trimmedMarkedBy.length >= 1 && trimmedMarkedBy.length <= MAX_MARKED_BY_LEN;
  const reasonValid = trimmedReason.length >= MIN_REASON_LEN;
  const canSubmit = markedByValid && reasonValid && !submitting;

  async function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    try {
      await onSubmit({ marked_by: trimmedMarkedBy, reason: trimmedReason });
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
      title="标记为误报"
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
            {submitting ? '提交中…' : '提交'}
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

      <div className="space-y-1.5">
        <Label htmlFor="fp-mark-marked-by">标记人</Label>
        <Input
          id="fp-mark-marked-by"
          value={markedBy}
          maxLength={MAX_MARKED_BY_LEN}
          onChange={(event) => setMarkedBy(event.target.value)}
        />
        {!markedByValid ? (
          <div className="text-[11px] text-rose-600">
            标记人不能为空，长度需在 1–{MAX_MARKED_BY_LEN} 字符之间。
          </div>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="fp-mark-reason">原因说明</Label>
        <Textarea
          id="fp-mark-reason"
          rows={4}
          value={reason}
          placeholder="请描述为什么这条问题是误报，例如：xx 场景下这段代码是安全的"
          onChange={(event) => setReason(event.target.value)}
        />
        {!reasonValid ? (
          <div className="text-[11px] text-rose-600">
            原因至少 {MIN_REASON_LEN} 字符——请具体说明为什么这是误报。
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
