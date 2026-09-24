/**
 * 实体状态标签（antd Tag 版）。
 *
 * 迁移自原 App.tsx 的 *BadgeProps 系列函数，语义保持一致：
 * - open / NONE 等默认态不渲染标签，避免视觉噪声；
 * - 未知值渲染中性灰标签 + 原字符串，便于及时发现后端新增状态。
 */

import { Tag } from 'antd';

type TagColor = 'red' | 'gold' | 'green' | 'blue' | 'purple' | 'cyan' | 'default';

export function SeverityTag({ severity }: { severity: string }) {
  const upper = (severity ?? '').toUpperCase();
  const color: TagColor =
    upper === 'BLOCKER' ? 'red' : upper === 'WARNING' ? 'gold' : upper === 'INFO' ? 'blue' : 'default';
  return <Tag color={color}>{upper || '—'}</Tag>;
}

export function FpStatusTag({ status }: { status: string | null | undefined }) {
  if (!status || status === 'NONE') {
    return null;
  }
  if (status === 'PENDING') {
    return <Tag color="gold">误报待审</Tag>;
  }
  if (status === 'CONFIRMED') {
    return <Tag color="green">已确认误报</Tag>;
  }
  if (status === 'REJECTED') {
    return <Tag color="red">误报驳回</Tag>;
  }
  return <Tag>{status}</Tag>;
}

export function FindingStatusTag({ status }: { status: string | null | undefined }) {
  if (!status || status === 'open') {
    return null;
  }
  if (status === 'resolved') {
    return <Tag color="green">已修复</Tag>;
  }
  if (status === 'mr_closed') {
    return <Tag>MR 已关闭</Tag>;
  }
  return <Tag>{status}</Tag>;
}

export function ReviewStatusTag({ status, hasBlocker }: { status: string; hasBlocker: boolean }) {
  if (status === 'engine_error') {
    if (hasBlocker) {
      return (
        <Tag color="red" title="AI 引擎调用失败，且策略阻止合并，请人工审查。">
          审查失败
        </Tag>
      );
    }
    return (
      <Tag color="gold" title="AI 引擎调用失败，策略未阻止合并，但请人工审查。">
        引擎异常
      </Tag>
    );
  }
  if (status === 'done') {
    return hasBlocker ? <Tag color="red">阻断</Tag> : <Tag color="green">通过</Tag>;
  }
  return <Tag title={`未知状态：${status}`}>未知</Tag>;
}

export function ReviewModeTag({
  mode,
  baseSha,
}: {
  mode: string | null | undefined;
  baseSha?: string | null;
}) {
  if (mode === 'incremental') {
    const title = baseSha ? `相较上次 push: ${baseSha.slice(0, 7)}` : '相较上次 push 的增量审查';
    return (
      <Tag color="blue" title={title}>
        增量
      </Tag>
    );
  }
  if (mode === 'reuse') {
    return (
      <Tag color="purple" title="复用自上一次同 commit 的审查">
        复用
      </Tag>
    );
  }
  if (mode && mode !== 'full') {
    return <Tag title={`未知 review_mode：${mode}`}>{mode}</Tag>;
  }
  return <Tag>全量</Tag>;
}

export function LifecycleEventTag({ event }: { event: string | null | undefined }) {
  if (event === 'mr_closed') {
    return (
      <Tag title="MR 关闭事件的生命周期记账，涉及的 finding 已标记为 mr_closed">MR 已关闭</Tag>
    );
  }
  if (event === 'mr_merged') {
    return (
      <Tag color="blue" title="MR 合并事件的生命周期记账，涉及的 finding 已标记为 resolved">
        MR 已合并
      </Tag>
    );
  }
  return null;
}

export function EnabledTag({ enabled }: { enabled: boolean }) {
  return enabled ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>;
}

/** 状态指示圆点（保留原 Linear 风格的「色点 + 文案」语义）。 */
export function StatusDot({ tone }: { tone: 'ok' | 'warn' | 'bad' | 'idle' }) {
  const color =
    tone === 'ok' ? '#10B981' : tone === 'warn' ? '#F59E0B' : tone === 'bad' ? '#EF4444' : '#D4D4D8';
  return (
    <span
      aria-hidden
      style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: color }}
    />
  );
}
