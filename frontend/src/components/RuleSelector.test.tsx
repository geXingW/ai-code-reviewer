import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RuleSelector } from './RuleSelector';
import type { RuleConfig } from '../api';

/** 构造 RuleConfig 测试样例，只需覆盖用到的字段。 */
function makeRule(overrides: Partial<RuleConfig> = {}): RuleConfig {
  return {
    id: overrides.id ?? 'r1-uuid',
    rule_id: overrides.rule_id ?? 'general.default-rule',
    title: overrides.title ?? '默认规则',
    prompt_snippet: overrides.prompt_snippet ?? '一段说明',
    severity_default: overrides.severity_default ?? 'WARNING',
    category_default: overrides.category_default ?? 'other',
    languages: overrides.languages ?? ['*'],
    path_patterns: overrides.path_patterns ?? [],
    enabled: overrides.enabled ?? true,
  };
}

const RULES: RuleConfig[] = [
  makeRule({
    id: 'u-1',
    rule_id: 'general.hardcoded-secret',
    title: '硬编码密钥',
    severity_default: 'BLOCKER',
    category_default: 'security',
    languages: ['*'],
  }),
  makeRule({
    id: 'u-2',
    rule_id: 'python.exception-handling',
    title: '异常处理不当',
    severity_default: 'WARNING',
    category_default: 'bug',
    languages: ['python'],
  }),
  makeRule({
    id: 'u-3',
    rule_id: 'js.n-plus-one',
    title: 'N+1 查询',
    severity_default: 'WARNING',
    category_default: 'performance',
    languages: ['javascript', 'typescript'],
  }),
  makeRule({
    id: 'u-4',
    rule_id: 'python.perf-loop',
    title: '低效循环',
    severity_default: 'INFO',
    category_default: 'performance',
    languages: ['python'],
  }),
  makeRule({
    id: 'u-5',
    rule_id: 'java.blocker-sql',
    title: 'SQL 注入',
    severity_default: 'BLOCKER',
    category_default: 'security',
    languages: ['java'],
  }),
];

afterEach(() => {
  vi.restoreAllMocks();
});

/** 获取可见规则行（label 元素）。 */
function getVisibleRuleLabels(): HTMLElement[] {
  return screen.getAllByRole('checkbox', { name: /选中规则/ }).map((cb) => cb.closest('label')!);
}

describe('RuleSelector', () => {
  it('搜索：按 rule_id / title 过滤，忽略大小写；空字符串显示全部', async () => {
    const user = userEvent.setup();
    render(
      <RuleSelector rules={RULES} selectedRuleIds={[]} onToggle={vi.fn()} onBulkReplace={vi.fn()} />,
    );
    expect(getVisibleRuleLabels()).toHaveLength(5);

    const input = screen.getByLabelText('搜索规则');
    await user.type(input, 'HARDCODED');
    expect(getVisibleRuleLabels()).toHaveLength(1);
    expect(screen.getByText('硬编码密钥')).toBeInTheDocument();

    await user.clear(input);
    expect(getVisibleRuleLabels()).toHaveLength(5);

    await user.type(input, '低效');
    expect(getVisibleRuleLabels()).toHaveLength(1);
    expect(screen.getByText('低效循环')).toBeInTheDocument();
  });

  it('严重度筛选：单选 BLOCKER 只显示 BLOCKER；多选 BLOCKER+INFO 显示两者并集', async () => {
    const user = userEvent.setup();
    render(
      <RuleSelector rules={RULES} selectedRuleIds={[]} onToggle={vi.fn()} onBulkReplace={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: '筛选严重度 BLOCKER' }));
    let labels = getVisibleRuleLabels();
    expect(labels).toHaveLength(2);
    expect(within(labels[0]).getByText(/硬编码密钥|SQL 注入/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '筛选严重度 INFO' }));
    labels = getVisibleRuleLabels();
    expect(labels).toHaveLength(3); // 2 BLOCKER + 1 INFO
  });

  it('分类筛选：勾选安全 chip 只显示 security；多选做 OR', async () => {
    const user = userEvent.setup();
    render(
      <RuleSelector rules={RULES} selectedRuleIds={[]} onToggle={vi.fn()} onBulkReplace={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: '筛选分类 安全' }));
    expect(getVisibleRuleLabels()).toHaveLength(2);

    await user.click(screen.getByRole('button', { name: '筛选分类 性能' }));
    expect(getVisibleRuleLabels()).toHaveLength(4); // security(2) + performance(2)
  });

  it('语言筛选：Python 只显示 python 或通用；JS/TS 合并；通用规则命中所有语言筛选', async () => {
    const user = userEvent.setup();
    render(
      <RuleSelector rules={RULES} selectedRuleIds={[]} onToggle={vi.fn()} onBulkReplace={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: '筛选语言 Python' }));
    // python(u-2, u-4) + 通用(u-1) = 3
    expect(getVisibleRuleLabels()).toHaveLength(3);

    // 关掉 Python，勾 JS/TS
    await user.click(screen.getByRole('button', { name: '筛选语言 Python' }));
    await user.click(screen.getByRole('button', { name: '筛选语言 JS/TS' }));
    // JS/TS(u-3) + 通用(u-1) = 2
    expect(getVisibleRuleLabels()).toHaveLength(2);
    expect(screen.getByText('N+1 查询')).toBeInTheDocument();
    expect(screen.getByText('硬编码密钥')).toBeInTheDocument();
  });

  it('多维筛选：BLOCKER + 安全 做 AND', async () => {
    const user = userEvent.setup();
    render(
      <RuleSelector rules={RULES} selectedRuleIds={[]} onToggle={vi.fn()} onBulkReplace={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: '筛选严重度 BLOCKER' }));
    await user.click(screen.getByRole('button', { name: '筛选分类 安全' }));
    expect(getVisibleRuleLabels()).toHaveLength(2);
  });

  it('全选可见：把可见规则并入 selected，不动不可见的历史选中', async () => {
    const user = userEvent.setup();
    const onBulkReplace = vi.fn();
    // 假设用户已勾选 u-3（js/ts），现在筛 Python 让 u-3 不可见后点全选可见。
    render(
      <RuleSelector
        rules={RULES}
        selectedRuleIds={['u-3']}
        onToggle={vi.fn()}
        onBulkReplace={onBulkReplace}
      />,
    );
    await user.click(screen.getByRole('button', { name: '筛选语言 Python' }));
    await user.click(screen.getByRole('button', { name: '全选可见' }));
    expect(onBulkReplace).toHaveBeenCalledTimes(1);
    const arg = new Set(onBulkReplace.mock.calls[0][0] as string[]);
    // u-3 保留 + 可见的 u-1(通用) / u-2 / u-4
    expect(arg.has('u-3')).toBe(true);
    expect(arg.has('u-1')).toBe(true);
    expect(arg.has('u-2')).toBe(true);
    expect(arg.has('u-4')).toBe(true);
    expect(arg.has('u-5')).toBe(false);
  });

  it('取消全选：清空 selectedRuleIds（即使处于筛选状态）', async () => {
    const user = userEvent.setup();
    const onBulkReplace = vi.fn();
    render(
      <RuleSelector
        rules={RULES}
        selectedRuleIds={['u-1', 'u-2', 'u-3']}
        onToggle={vi.fn()}
        onBulkReplace={onBulkReplace}
      />,
    );
    await user.click(screen.getByRole('button', { name: '筛选严重度 BLOCKER' }));
    await user.click(screen.getByRole('button', { name: '取消全选' }));
    expect(onBulkReplace).toHaveBeenCalledWith([]);
  });

  it('勾选可见 BLOCKER：只并入可见的 BLOCKER；其他 selected 不动', async () => {
    const user = userEvent.setup();
    const onBulkReplace = vi.fn();
    render(
      <RuleSelector
        rules={RULES}
        selectedRuleIds={['u-2']}
        onToggle={vi.fn()}
        onBulkReplace={onBulkReplace}
      />,
    );
    // 先筛 Python：只有 u-1(通用) / u-2 / u-4 可见——其中只有 u-1 是 BLOCKER。
    await user.click(screen.getByRole('button', { name: '筛选语言 Python' }));
    await user.click(screen.getByRole('button', { name: '勾选可见 BLOCKER' }));
    const arg = new Set(onBulkReplace.mock.calls[0][0] as string[]);
    expect(arg.has('u-1')).toBe(true);
    expect(arg.has('u-2')).toBe(true);
    expect(arg.has('u-5')).toBe(false);
  });

  it('计数栏：已选 / 可见 / 总数正确', async () => {
    const user = userEvent.setup();
    render(
      <RuleSelector
        rules={RULES}
        selectedRuleIds={['u-1', 'u-5']}
        onToggle={vi.fn()}
        onBulkReplace={vi.fn()}
      />,
    );
    expect(screen.getByText(/已选/)).toHaveTextContent('已选 2 / 可见 5 / 总 5');
    await user.click(screen.getByRole('button', { name: '筛选严重度 BLOCKER' }));
    expect(screen.getByText(/已选/)).toHaveTextContent('已选 2 / 可见 2 / 总 5');
  });

  it('清除所有筛选：搜索 + 所有 chip 状态复位', async () => {
    const user = userEvent.setup();
    render(
      <RuleSelector rules={RULES} selectedRuleIds={[]} onToggle={vi.fn()} onBulkReplace={vi.fn()} />,
    );
    const input = screen.getByLabelText('搜索规则');
    await user.type(input, 'python');
    await user.click(screen.getByRole('button', { name: '筛选严重度 WARNING' }));
    expect(getVisibleRuleLabels().length).toBeLessThan(5);

    await user.click(screen.getAllByRole('button', { name: '清除所有筛选' })[0]);
    expect((input as HTMLInputElement).value).toBe('');
    expect(getVisibleRuleLabels()).toHaveLength(5);
  });

  it('空态：无规则显示空提示', () => {
    render(<RuleSelector rules={[]} selectedRuleIds={[]} onToggle={vi.fn()} onBulkReplace={vi.fn()} />);
    expect(screen.getByText(/暂无规则/)).toBeInTheDocument();
  });

  it('空态：筛选后无匹配显示相应提示', async () => {
    const user = userEvent.setup();
    render(
      <RuleSelector rules={RULES} selectedRuleIds={[]} onToggle={vi.fn()} onBulkReplace={vi.fn()} />,
    );
    const input = screen.getByLabelText('搜索规则');
    await user.type(input, '这个字符串一定匹配不到任何规则xyzq');
    expect(screen.getByText(/当前筛选条件无匹配规则/)).toBeInTheDocument();
  });

  it('单条勾选：触发 onToggle 并携带 rule.id 和新状态', () => {
    const onToggle = vi.fn();
    render(
      <RuleSelector rules={RULES} selectedRuleIds={[]} onToggle={onToggle} onBulkReplace={vi.fn()} />,
    );
    const checkbox = screen.getByRole('checkbox', { name: '选中规则 general.hardcoded-secret' });
    fireEvent.click(checkbox);
    expect(onToggle).toHaveBeenCalledWith('u-1', true);
  });
});
