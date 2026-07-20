/**
 * 「确认 / 驳回 误报」审核弹窗。
 *
 * 之前的旧流程：误报队列列表直接点按钮就 confirm/reject。这里补：
 *   - 展示 finding 上下文 + 提交人 / 提交时间 / 提交原因；
 *   - 审核人默认取 sessionStorage 里登录用户名，可编辑但必填；
 *   - 审核备注：confirm 时非必填（"没什么可写"也放行 → 会走 fp_marked_reason 兜底
 *     作为负例 explanation）；reject 时必填 >= 5 字符——驳回没理由是最容易吵架的地方。
 *   - action='confirm' 灰蓝底提示条说明会沉淀为负例；action='reject' 提示条说明状态回滚。
 */

import { useEffect, useState } from 'react';

import { FindingRecord } from '../../api';
import { relativeTime } from '../../App';
import { Button } from '../ui/button';
import { Dialog } from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';
import { severityDisplay } from '../../lib/findingTaxonomy';

export interface ReviewFalsePositiveDialogProps {
  open: boolean;
  finding: FindingRecord | null;
  action: 'confirm' | 'reject';
  defaultReviewedBy: string;
  onCancel: () => void;
  onSubmit: (payload: { reviewed_by: string; note: string }) => Promise<void>;
}

const MIN_NOTE_LEN = 5;
const MAX_REVIEWED_BY_LEN = 255;

export function ReviewFalsePositiveDialog({
  open,
  finding,
  action,
  defaultReviewedBy,
  onCancel,
  onSubmit,
}: ReviewFalsePositiveDialogProps) {
  const [reviewedBy, setReviewedBy] = useState(defaultReviewedBy);
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setReviewedBy(defaultReviewedBy);
      setNote('');
      setErrorMessage(null);
      setSubmitting(false);
    }
  }, [open, defaultReviewedBy, action]);

  const trimmedReviewedBy = reviewedBy.trim();
  const trimmedNote = note.trim();
  const reviewedByValid =
    trimmedReviewedBy.length >= 1 && trimmedReviewedBy.length <= MAX_REVIEWED_BY_LEN;
  // confirm 时 note 非必填；reject 时至少 5 字符（驳回必须有理由）。
  const noteValid =
    action === 'confirm' ? true : trimmedNote.length >= MIN_NOTE_LEN;
  const canSubmit = reviewedByValid && noteValid && !submitting;

  async function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    try {
      await onSubmit({ reviewed_by: trimmedReviewedBy, note: trimmedNote });
    } catch (caught) {
      setErrorMessage(caught instanceof Error ? caught.message : '提交失败');
    } finally {
      setSubmitting(false);
    }
  }

  const sev = finding ? severityDisplay(finding.severity) : null;
  const title = action === 'confirm' ? '确认误报（将沉淀为负例）' : '驳回误报';
  const primaryLabel = action === 'confirm' ? '确认误报' : '驳回';
  const submittingLabel = '提交中…';

  return (
    <Dialog
      open={open}
      onClose={submitting ? () => {} : onCancel}
      title={title}
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
            variant={action === 'reject' ? 'destructive' : 'default'}
            onClick={() => void handleSubmit()}
          >
            {submitting ? submittingLabel : primaryLabel}
          </Button>
        </>
      }
    >
      {finding ? (
        <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3 text-[12px] text-zinc-700 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            {sev ? (
              <span className="inline-flex items-center gap-1">
                <span aria-hidden>{sev.emoji}</span>
                <span className="font-medium">{sev.label}</span>
              </span>
            ) : null}
            <span className="text-zinc-500">·</span>
            <span className="font-mono text-[11px] text-zinc-600">
              {finding.file_path}:{finding.line_number ?? '-'} · {finding.rule_id}
            </span>
          </div>
          <div className="text-[11px] text-zinc-500">
            {finding.project_name ?? '未知项目'}
            {finding.mr_iid ? ` · MR !${finding.mr_iid}` : ''}
          </div>
          <div className="mt-2 border-t border-zinc-200 pt-2 space-y-1">
            <div className="text-[11px] text-zinc-500">
              提交人 <span className="text-zinc-800">{finding.fp_marked_by ?? '未知'}</span>
              <span className="mx-1">·</span>
              提交时间 <span className="text-zinc-800">{relativeTime(finding.fp_marked_at ?? undefined) || '未知'}</span>
            </div>
            <div>
              <div className="text-[11px] text-zinc-500 mb-1">提交原因</div>
              <div className="rounded bg-white border border-zinc-200 px-2 py-1.5 font-mono text-[12px] text-zinc-800 whitespace-pre-wrap break-words">
                {finding.fp_marked_reason ?? '（未填写）'}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {action === 'confirm' ? (
        <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-[12px] text-slate-700">
          确认后本 finding 的代码片段将作为负例注入到该规则未来的评审 prompt 中。
        </div>
      ) : (
        <div className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-[12px] text-zinc-700">
          驳回后 finding 状态回到普通问题，标记人可继续与规则维护者沟通。
        </div>
      )}

      <div className="space-y-1.5">
        <Label htmlFor="fp-review-reviewed-by">审核人</Label>
        <Input
          id="fp-review-reviewed-by"
          value={reviewedBy}
          maxLength={MAX_REVIEWED_BY_LEN}
          onChange={(event) => setReviewedBy(event.target.value)}
        />
        {!reviewedByValid ? (
          <div className="text-[11px] text-rose-600">
            审核人不能为空，长度需在 1–{MAX_REVIEWED_BY_LEN} 字符之间。
          </div>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="fp-review-note">
          审核备注
          {action === 'reject' ? (
            <span className="ml-1 text-rose-600">*</span>
          ) : (
            <span className="ml-1 text-zinc-400">（建议填写）</span>
          )}
        </Label>
        <Textarea
          id="fp-review-note"
          rows={4}
          value={note}
          placeholder={
            action === 'confirm'
              ? '可补充负例说明，将写入 NegativeExample.explanation。'
              : '请说明驳回原因，例如：该场景确实需要 AI 提醒。'
          }
          onChange={(event) => setNote(event.target.value)}
        />
        {!noteValid ? (
          <div className="text-[11px] text-rose-600">
            驳回时备注至少 {MIN_NOTE_LEN} 字符——请给出明确理由。
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
