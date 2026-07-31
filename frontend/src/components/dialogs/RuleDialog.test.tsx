/**
 * RuleDialog：标签输入交互测试。
 *
 * 关键契约：
 *   1. 新增模式输入标签并保存 -> onSubmit payload.tags 包含该标签。
 *   2. 重复标签自动去重（同一标签只保留一个）。
 *   3. 编辑模式回显已有标签。
 *   4. 删除标签后提交 -> payload.tags 不含该标签。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { RuleDialog } from './RuleDialog';
import type { RuleConfig, RuleFormPayload } from '../../api';

/** 构造 RuleConfig 测试样例（编辑模式回显用，覆盖必填字段）。 */
function makeRule(overrides: Partial<RuleConfig> = {}): RuleConfig {
  return {
    id: overrides.id ?? 'r-uuid',
    rule_id: overrides.rule_id ?? 'test.demo-rule',
    title: overrides.title ?? '示例规则',
    prompt_snippet: overrides.prompt_snippet ?? '一段提示词片段',
    severity_default: overrides.severity_default ?? 'WARNING',
    category_default: overrides.category_default ?? null,
    languages: overrides.languages ?? [],
    path_patterns: overrides.path_patterns ?? [],
    tags: overrides.tags ?? [],
    enabled: overrides.enabled ?? true,
  };
}

/** 新增模式下填入合法的标题与提示片段，使「保存」按钮可用。 */
async function fillRequiredFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('标题'), '检测硬编码密钥');
  await user.type(screen.getByLabelText('提示片段'), '检查代码里是否硬编码了密钥');
}

describe('RuleDialog 标签输入', () => {
  it('新增模式：添加标签后保存，payload.tags 包含该标签', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<RuleDialog open initialData={null} onCancel={vi.fn()} onSubmit={onSubmit} />);

    await fillRequiredFields(user);
    await user.type(screen.getByLabelText(/标签/), 'security');
    await user.click(screen.getByRole('button', { name: '添加标签' }));

    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0] as RuleFormPayload;
    expect(payload.tags).toEqual(['security']);
  });

  it('新增模式：重复标签自动去重', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<RuleDialog open initialData={null} onCancel={vi.fn()} onSubmit={onSubmit} />);

    await fillRequiredFields(user);
    const tagInput = screen.getByLabelText(/标签/);
    // 连续添加两次 security，应只保留一个。
    await user.type(tagInput, 'security');
    await user.click(screen.getByRole('button', { name: '添加标签' }));
    await user.type(tagInput, 'security');
    await user.click(screen.getByRole('button', { name: '添加标签' }));

    // 用「删除标签 security」按钮计数，最稳地断言只有一个 pill。
    expect(screen.getAllByRole('button', { name: '删除标签 security' })).toHaveLength(1);

    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0] as RuleFormPayload;
    expect(payload.tags).toEqual(['security']);
  });

  it('编辑模式：回显已有标签', () => {
    render(
      <RuleDialog
        open
        initialData={makeRule({ tags: ['security', 'python'] })}
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    // 两个标签 pill 均渲染，各自带删除按钮。
    expect(screen.getByRole('button', { name: '删除标签 security' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '删除标签 python' })).toBeInTheDocument();
  });

  it('删除标签功能正常：删除后提交 payload 不含该标签', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <RuleDialog
        open
        initialData={makeRule({ tags: ['security', 'python'] })}
        onCancel={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.click(screen.getByRole('button', { name: '删除标签 security' }));
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0] as RuleFormPayload;
    expect(payload.tags).toEqual(['python']);
  });
});
