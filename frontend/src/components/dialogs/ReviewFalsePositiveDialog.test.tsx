/**
 * ReviewFalsePositiveDialog：审核弹窗对 confirm / reject 两种 action 的表单契约。
 *
 * 关键点：
 *   1. open=false 不渲染。
 *   2. confirm 时按钮为普通样式、显示"确认误报"；reject 显示"驳回"。
 *   3. confirm 时备注非必填——空备注也可点提交。
 *   4. reject 时备注必填且 >= 5 字符，否则 disabled。
 *   5. 提交 payload 会 trim；action 由父组件持有。
 *   6. onSubmit 抛错时展示行内错误。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ReviewFalsePositiveDialog } from './ReviewFalsePositiveDialog';
import type { FindingRecord } from '../../api';

function makeFinding(overrides: Partial<FindingRecord> = {}): FindingRecord {
  return {
    id: 'f-2',
    review_id: 'r-2',
    rule_id: 'no-eval',
    title: 'eval 存在注入风险',
    severity: 'BLOCKER',
    file_path: 'src/bar.py',
    line_number: 7,
    description: '',
    suggestion: '',
    fp_status: 'PENDING',
    fp_marked_by: 'bob',
    fp_marked_at: '2026-07-19T10:00:00Z',
    fp_marked_reason: '走的是白名单参数',
    status: 'open',
    ...overrides,
  } as FindingRecord;
}

describe('ReviewFalsePositiveDialog', () => {
  it('open=false 时不渲染 dialog', () => {
    render(
      <ReviewFalsePositiveDialog
        open={false}
        finding={makeFinding()}
        action="confirm"
        defaultReviewedBy="admin"
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('confirm 模式渲染"确认误报"标题、按钮，以及沉淀说明提示条', () => {
    render(
      <ReviewFalsePositiveDialog
        open
        finding={makeFinding()}
        action="confirm"
        defaultReviewedBy="reviewer"
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/确认误报（将沉淀为负例）/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认误报' })).toBeInTheDocument();
    // 显示提交人 / 提交原因等 finding 上下文。
    expect(screen.getByText(/走的是白名单参数/)).toBeInTheDocument();
    expect(screen.getByText(/bob/)).toBeInTheDocument();
  });

  it('reject 模式渲染"驳回"标题和红色变体按钮', () => {
    render(
      <ReviewFalsePositiveDialog
        open
        finding={makeFinding()}
        action="reject"
        defaultReviewedBy="reviewer"
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByText('驳回误报')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '驳回' })).toBeInTheDocument();
  });

  it('confirm 时备注可以为空也能提交', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <ReviewFalsePositiveDialog
        open
        finding={makeFinding()}
        action="confirm"
        defaultReviewedBy="reviewer"
        onCancel={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    const submit = screen.getByRole('button', { name: '确认误报' });
    expect(submit).not.toBeDisabled();
    await userEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith({ reviewed_by: 'reviewer', note: '' });
  });

  it('reject 时备注 < 5 字符则禁用提交，>=5 字符解锁', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <ReviewFalsePositiveDialog
        open
        finding={makeFinding()}
        action="reject"
        defaultReviewedBy="reviewer"
        onCancel={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    const submit = screen.getByRole('button', { name: '驳回' });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/审核备注/), '不同意');
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/审核备注/), '，这是真问题');
    expect(submit).not.toBeDisabled();
    await userEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      reviewed_by: 'reviewer',
      note: '不同意，这是真问题',
    });
  });

  it('审核人为空时提交按钮禁用', async () => {
    render(
      <ReviewFalsePositiveDialog
        open
        finding={makeFinding()}
        action="confirm"
        defaultReviewedBy=""
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: '确认误报' })).toBeDisabled();
    expect(screen.getByText(/审核人不能为空/)).toBeInTheDocument();
  });

  it('onSubmit 抛错时展示行内错误信息', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('确认失败'));
    render(
      <ReviewFalsePositiveDialog
        open
        finding={makeFinding()}
        action="confirm"
        defaultReviewedBy="reviewer"
        onCancel={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: '确认误报' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('确认失败'));
  });
});
