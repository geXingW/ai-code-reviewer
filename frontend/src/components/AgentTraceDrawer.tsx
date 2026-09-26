/**
 * 「Agent 执行轨迹」抽屉：展示 llm-agent 引擎一次审查的中间过程——
 * 每轮模型响应、工具调用的参数/输出/耗时、最终结论，数据来自
 * GET /api/reviews/{id}/agent-trace（按 seq 升序）。
 * 挂载在审查记录页的行展开区；llm-direct 引擎等无轨迹时展示空态。
 */

import { useEffect, useState } from 'react';
import { Alert, Drawer, Skeleton, Space, Tag, Timeline } from 'antd';

import { fetchReviewAgentTrace, type AgentTraceEvent, type AgentTraceEventType } from '../api';

const EVENT_LABELS: Record<AgentTraceEventType, string> = {
  run_started: '开始',
  llm_response: '模型响应',
  tool_executed: '工具调用',
  run_finished: '完成',
  run_failed: '失败',
};

const STATUS_COLORS: Record<string, string> = {
  ok: 'green',
  timeout: 'orange',
  error: 'red',
  malformed_arguments: 'orange',
  unknown_tool: 'red',
  budget_exhausted: 'orange',
};

type RunStartedPayload = {
  provider_type?: string;
  model?: string;
  max_turns?: number;
  tool_names?: string[];
  system_prompt_chars?: number;
  user_prompt_chars?: number;
  budget_max_chars?: number;
};

type LlmResponsePayload = {
  content?: string;
  tool_calls?: { id?: string; name?: string; arguments?: string }[];
  usage?: Record<string, number>;
  model?: string;
  message_count?: number;
  is_closeout?: boolean;
};

type ToolExecutedPayload = {
  call_id?: string;
  raw_arguments?: string;
  arguments?: unknown;
  output?: string;
  budget_used?: number;
  budget_max_chars?: number;
};

type RunFinishedPayload = {
  turns_used?: number;
  findings_count?: number;
  findings_before_filter?: number | null;
  final_text?: string | null;
  filter_applied?: boolean;
};

type RunFailedPayload = {
  reason?: string;
  error?: string | null;
  turns_used?: number;
};

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** 可折叠的原文块：默认收起，避免长输出把时间线撑爆。 */
function PayloadBlock({ label, content }: { label: string; content: string }) {
  if (!content) {
    return null;
  }
  return (
    <details className="mt-1">
      <summary className="cursor-pointer select-none text-[11px] text-zinc-500 hover:text-zinc-700">
        {label}
      </summary>
      <pre className="mt-1 max-h-72 overflow-auto rounded bg-zinc-50 p-2 font-mono text-[11px] leading-4 whitespace-pre-wrap break-all text-zinc-700">
        {content}
      </pre>
    </details>
  );
}

function TurnChip({ turn }: { turn: number | null }) {
  return turn === null ? null : <Tag className="mr-1">第 {turn + 1} 轮</Tag>;
}

function eventTitle(event: AgentTraceEvent) {
  const duration =
    event.duration_ms !== null && event.duration_ms !== undefined ? (
      <span className="text-[11px] text-zinc-400">{event.duration_ms}ms</span>
    ) : null;

  if (event.event_type === 'tool_executed') {
    return (
      <Space size={6} wrap>
        <TurnChip turn={event.turn} />
        <span className="font-mono text-[12px] font-medium text-zinc-800">{event.tool_name}</span>
        {event.status && event.status !== 'ok' ? (
          <Tag color={STATUS_COLORS[event.status] ?? 'default'}>{event.status}</Tag>
        ) : null}
        {duration}
      </Space>
    );
  }
  if (event.event_type === 'llm_response') {
    const payload = (event.payload ?? {}) as LlmResponsePayload;
    return (
      <Space size={6} wrap>
        <TurnChip turn={event.turn} />
        <span className="text-[12px] font-medium text-zinc-800">
          {payload.is_closeout ? '模型响应（强制收口）' : '模型响应'}
        </span>
        {(payload.tool_calls ?? []).map((call) => (
          <Tag key={call.id ?? call.name} color="blue">
            {call.name}
          </Tag>
        ))}
        {duration}
      </Space>
    );
  }
  if (event.event_type === 'run_failed') {
    return (
      <span className="text-[12px] font-medium text-red-600">
        {EVENT_LABELS[event.event_type]}
      </span>
    );
  }
  return (
    <span className="text-[12px] font-medium text-zinc-800">{EVENT_LABELS[event.event_type]}</span>
  );
}

function eventChildren(event: AgentTraceEvent) {
  const payload = event.payload ?? {};

  if (event.event_type === 'run_started') {
    const p = payload as RunStartedPayload;
    return (
      <div className="text-[11px] leading-5 text-zinc-500">
        引擎 llm-agent · 模型 {p.model ?? '-'} · 最大轮数 {p.max_turns ?? '-'}
        <br />
        system prompt {p.system_prompt_chars ?? '-'} 字符 · user prompt{' '}
        {p.user_prompt_chars ?? '-'} 字符 · 观察预算 {p.budget_max_chars ?? '-'} 字符
        <br />
        可用工具：{(p.tool_names ?? []).join('、') || '-'}
      </div>
    );
  }
  if (event.event_type === 'llm_response') {
    const p = payload as LlmResponsePayload;
    const usageEntries = Object.entries(p.usage ?? {});
    return (
      <div className="text-[11px] leading-5 text-zinc-500">
        消息数 {p.message_count ?? '-'}
        {usageEntries.length > 0 ? ` · tokens ${usageEntries.map(([k, v]) => `${k}=${v}`).join(', ')}` : ''}
        <PayloadBlock label="回复原文" content={p.content ?? ''} />
      </div>
    );
  }
  if (event.event_type === 'tool_executed') {
    const p = payload as ToolExecutedPayload;
    return (
      <div className="text-[11px] leading-5 text-zinc-500">
        {typeof p.budget_used === 'number' && typeof p.budget_max_chars === 'number' ? (
          <>观察预算已用 {p.budget_used} / {p.budget_max_chars} 字符<br /></>
        ) : null}
        <PayloadBlock label="参数" content={formatJson(p.arguments ?? p.raw_arguments ?? '')} />
        <PayloadBlock label="输出（回填给模型的内容）" content={p.output ?? ''} />
      </div>
    );
  }
  if (event.event_type === 'run_finished') {
    const p = payload as RunFinishedPayload;
    return (
      <div className="text-[11px] leading-5 text-zinc-500">
        输出问题 {p.findings_count ?? 0} 个
        {p.filter_applied && p.findings_before_filter != null
          ? `（证伪过滤前 ${p.findings_before_filter} 个）`
          : ''}
        {' · '}循环 {p.turns_used ?? '-'} 轮
        <PayloadBlock label="最终回复" content={p.final_text ?? ''} />
      </div>
    );
  }
  if (event.event_type === 'run_failed') {
    const p = payload as RunFailedPayload;
    return (
      <div className="text-[11px] leading-5 text-red-500">
        原因 {p.reason ?? '-'} · 已进行 {p.turns_used ?? 0} 轮
        {p.error ? <PayloadBlock label="错误详情" content={p.error} /> : null}
      </div>
    );
  }
  return null;
}

const EVENT_DOT_COLORS: Record<AgentTraceEventType, string> = {
  run_started: 'blue',
  llm_response: 'blue',
  tool_executed: 'gray',
  run_finished: 'green',
  run_failed: 'red',
};

export interface AgentTraceDrawerProps {
  reviewId: string | null;
  onClose: () => void;
}

export function AgentTraceDrawer({ reviewId, onClose }: AgentTraceDrawerProps) {
  const [loading, setLoading] = useState(false);
  const [events, setEvents] = useState<AgentTraceEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!reviewId) {
      return;
    }
    let active = true;
    setLoading(true);
    setError(null);
    setEvents([]);
    fetchReviewAgentTrace(reviewId)
      .then((items) => {
        if (active) setEvents(items);
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : '加载执行轨迹失败');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reviewId]);

  return (
    <Drawer
      title="Agent 执行轨迹"
      width={720}
      open={reviewId !== null}
      onClose={onClose}
      destroyOnHidden
    >
      {loading ? <Skeleton active paragraph={{ rows: 6 }} /> : null}
      {!loading && error ? <Alert type="error" showIcon message={error} /> : null}
      {!loading && !error && events.length === 0 ? (
        <div className="py-6 text-center text-[12px] text-zinc-400">
          暂无执行轨迹（仅 llm-agent 引擎产生轨迹，且需后端开启 AGENT_TRACE_* 配置）
        </div>
      ) : null}
      {!loading && !error && events.length > 0 ? (
        <Timeline
          items={events.map((event) => ({
            key: event.id,
            color: STATUS_COLORS[event.status ?? ''] ?? EVENT_DOT_COLORS[event.event_type],
            children: (
              <div className="pb-1">
                {eventTitle(event)}
                {eventChildren(event)}
              </div>
            ),
          }))}
        />
      ) : null}
    </Drawer>
  );
}
