import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import { AgentTraceDrawer } from './AgentTraceDrawer';
import { fetchReviewAgentTrace, type AgentTraceEvent } from '../api';

vi.mock('../api', () => ({
  fetchReviewAgentTrace: vi.fn(),
}));

const mockedFetch = vi.mocked(fetchReviewAgentTrace);

function traceEvent(overrides: Partial<AgentTraceEvent>): AgentTraceEvent {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    review_id: '00000000-0000-0000-0000-000000000002',
    seq: 2,
    turn: 0,
    event_type: 'llm_response',
    tool_name: null,
    status: null,
    duration_ms: 1000,
    payload: {},
    created_at: '2026-10-12T02:30:21+00:00',
    ...overrides,
  } as AgentTraceEvent;
}

function renderWithEvents(events: AgentTraceEvent[]) {
  mockedFetch.mockResolvedValue(events);
  render(<AgentTraceDrawer reviewId="r1" onClose={() => {}} />);
}

describe('AgentTraceDrawer 模型响应内容渲染', () => {
  it('思考模式轮次渲染「思考过程」折叠块', async () => {
    renderWithEvents([
      traceEvent({
        payload: {
          content: '',
          reasoning_content: 'need to check the caller of login()',
          tool_calls: [{ id: 'call-1', name: 'read_file', arguments: '{}' }],
          usage: { total_tokens: 13005 },
          model: 'qwen3.7-plus',
          message_count: 1,
          is_closeout: false,
        },
      }),
    ]);

    expect(await screen.findByText('思考过程')).toBeTruthy();
    // content 为空但有思考时：不显示空态提示（思考过程本身就是内容）。
    expect(screen.queryByText('本轮无文本回复（仅工具调用）')).toBeNull();
  });

  it('普通轮次渲染「回复原文」折叠块', async () => {
    renderWithEvents([
      traceEvent({
        payload: {
          content: '{"findings": []}',
          tool_calls: [],
          usage: { total_tokens: 100 },
          model: 'qwen3.7-plus',
          message_count: 3,
          is_closeout: true,
        },
      }),
    ]);

    expect(await screen.findByText('回复原文')).toBeTruthy();
    expect(screen.queryByText('思考过程')).toBeNull();
    expect(screen.queryByText('本轮无文本回复')).toBeNull();
  });

  it('content 与思考均空且无工具调用时显示空态提示', async () => {
    renderWithEvents([
      traceEvent({
        payload: {
          content: '',
          tool_calls: [],
          usage: {},
          model: 'qwen3.7-plus',
          message_count: 1,
          is_closeout: false,
        },
      }),
    ]);

    expect(await screen.findByText('本轮无文本回复')).toBeTruthy();
  });
});
