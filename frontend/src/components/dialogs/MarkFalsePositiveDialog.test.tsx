/**
 * MarkFalsePositiveDialog：确认弹窗做到"必须显式给理由才能提交"。
 *
 * 关键契约：
 *   1. open=false 时 DOM 里不渲染 dialog（配合 UI 测试排除干扰）。
 *   2. 打开时预填 defaultMarkedBy。
 *   3. 标记人为空 / 只填空白时提交按钮 disabled。
 *   4. 原因 < 5 字符时提交按钮 disabled，并展示提示。
 *   5. 表单合法时点击提交会调用 onSubmit，payload trim 过。
 *   6. onSubmit 抛错时 dialog 展示行内错误、按钮回到可点击。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { MarkFalsePositiveDialog } from './MarkFalsePositiveDialog';
import type { FindingRecord } from '../../api';

function makeFinding(overrides: Partial<FindingRecord> = {}): FindingRecord {
  return {
    id: 'f-1',
    review_id: 'r-1',
    rule_id: 'no-print',
    title: 'print 语句未清理',
    severity: 'WARNING',
    file_path: 'src/foo.py',
    line_number: 42,
    description: '生产环境 print 会污染日志',
    suggestion: '改成 logger.debug',
    fp_status: 'NONE',
    status: 'open',
    ...overrides,
  } as FindingRecord;
}

describe('MarkFalsePositiveDialog', () => {
  it('open=false 时 dialog 不渲染到 DOM', () => {
    render(
      <MarkFalsePositiveDialog
        open={false}
        finding={makeFinding()}
        defaultMarkedBy="admin"
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('打开时预填 defaultMarkedBy 并渲染 finding 上下文', () => {
    render(
      <MarkFalsePositiveDialog
        open
        finding={makeFinding()}
        defaultMarkedBy="alice"
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText('标记人')).toHaveValue('alice');
    // 上下文卡片渲染文件路径和 rule_id。
    expect(screen.getByText(/src\/foo\.py:42/)).toBeInTheDocument();
    expect(screen.getByText(/no-print/)).toBeInTheDocument();
  });

  it('标记人为空时提交按钮 disabled 并展示错误提示', async () => {
    render(
      <MarkFalsePositiveDialog
        open
        finding={makeFinding()}
        defaultMarkedBy=""
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    const submitButton = screen.getByRole('button', { name: '提交' });
    expect(submitButton).toBeDisabled();
    expect(screen.getByText(/标记人不能为空/)).toBeInTheDocument();
  });

  it('原因少于 5 字符时提交按钮 disabled 并提示', async () => {
    render(
      <MarkFalsePositiveDialog
        open
        finding={makeFinding()}
        defaultMarkedBy="admin"
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    const reason = screen.getByLabelText('原因说明');
    await userEvent.type(reason, '短');
    expect(screen.getByRole('button', { name: '提交' })).toBeDisabled();
    expect(screen.getByText(/原因至少 5 字符/)).toBeInTheDocument();
  });

  it('表单合法时点击提交调用 onSubmit，payload 会 trim 空白', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <MarkFalsePositiveDialog
        open
        finding={makeFinding()}
        defaultMarkedBy="  admin  "
        onCancel={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    await userEvent.clear(screen.getByLabelText('标记人'));
    await userEvent.type(screen.getByLabelText('标记人'), 'alice');
    await userEvent.type(screen.getByLabelText('原因说明'), '  该分支是内部工具无需检测  ');
    const submit = screen.getByRole('button', { name: '提交' });
    expect(submit).not.toBeDisabled();
    await userEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith({
      marked_by: 'alice',
      reason: '该分支是内部工具无需检测',
    });
  });

  it('onSubmit 抛错时 dialog 展示行内错误并恢复可点击状态', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('后端说不行'));
    render(
      <MarkFalsePositiveDialog
        open
        finding={makeFinding()}
        defaultMarkedBy="admin"
        onCancel={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    await userEvent.type(screen.getByLabelText('原因说明'), '这段真的是安全的');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('后端说不行'));
    // 提交完成后按钮回到 "提交" 而非 "提交中…"。
    expect(screen.getByRole('button', { name: '提交' })).not.toBeDisabled();
  });
});
