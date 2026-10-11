/**
 * 「Push/Commit 审查」页：服务端分页表格，只显示 review_kind='commit'/'push' 的审查。
 *
 * 与「审查记录」页（MR 审查）并列的独立菜单。数据同源（/api/reviews/records），
 * 差异在：
 * - 固定按 review_kind=commit+push 过滤（一次 push 的合并审查 + 逐 commit 审查）；
 * - 首列渲染 commit 短 SHA（JetBrains Mono）+ 分支 + 来源徽章（Push/Commit），
 *   不渲染 MR 编号（mr_iid 恒为 NULL）；
 * - 行展开懒加载 findings + Agent 执行轨迹入口（AgentTraceDrawer），
 *   方便排查 push 审查链路问题；
 * - 筛选：项目 / 状态 / 来源（Push/Commit/全部）/ commit SHA 精确搜索。
 */

import { useEffect, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import { App as AntApp, Alert, Button, Card, Input, Select, Space, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  fetchProjectsAll,
  fetchReviewFindings,
  fetchReviewRecords,
  type FindingRecord,
  type ProjectConfig,
  type ReviewRecord,
} from '../api';
import { AgentTraceDrawer } from '../components/AgentTraceDrawer';
import { usePagedList, useDebouncedValue } from '../hooks/usePagedList';
import { relativeTime } from '../lib/format';
import { ReviewStatusTag, SeverityTag, StatusDot } from '../components/entityTags';

type Filters = {
  project_id: string;
  status: string;
  review_kind: string;
  commit_sha: string;
};

const STATUS_OPTIONS = [
  { value: 'pending', label: '待处理' },
  { value: 'running', label: '审查中' },
  { value: 'done', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'engine_error', label: '引擎异常' },
];

const KIND_OPTIONS = [
  { value: 'commit', label: 'Commit（逐个审查）' },
  { value: 'push', label: 'Push（合并审查）' },
];

interface ExpandedFindings {
  loading: boolean;
  items: FindingRecord[];
  error: string | null;
}

function statusTone(record: ReviewRecord): 'ok' | 'warn' | 'bad' | 'idle' {
  if (record.status === 'engine_error') {
    return record.has_blocker ? 'bad' : 'warn';
  }
  if (record.status === 'done') {
    return record.has_blocker ? 'bad' : 'ok';
  }
  return 'idle';
}

function KindTag({ kind }: { kind: string | null | undefined }) {
  if (kind === 'push') {
    return <Tag color="blue">Push</Tag>;
  }
  if (kind === 'commit') {
    return <Tag color="cyan">Commit</Tag>;
  }
  return null;
}

export function CommitReviewsPage() {
  const { message } = AntApp.useApp();
  const [projects, setProjects] = useState<ProjectConfig[]>([]);
  const [expanded, setExpanded] = useState<Record<string, ExpandedFindings>>({});
  const [traceReviewId, setTraceReviewId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchProjectsAll()
      .then((page) => {
        if (active) setProjects(page.items);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  const { items, total, loading, error, filters, setFilters, setSort, reload, pagination } =
    usePagedList<ReviewRecord, Filters>(
      ({ limit, offset, sort, filters: current }) =>
        fetchReviewRecords({
          limit,
          offset,
          sort,
          project_id: current.project_id || undefined,
          status: current.status || undefined,
          review_kind:
            current.review_kind === 'commit' || current.review_kind === 'push'
              ? [current.review_kind]
              : ['commit', 'push'],
          commit_sha: current.commit_sha || undefined,
        }),
      {
        project_id: '',
        status: '',
        review_kind: '',
        commit_sha: '',
      },
    );

  const [shaInput, setShaInput] = useState('');
  const debouncedSha = useDebouncedValue(shaInput, 300);
  useEffect(() => {
    if ((filters.commit_sha ?? '') !== debouncedSha) {
      setFilters({ commit_sha: debouncedSha });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSha]);

  async function loadExpandedFindings(record: ReviewRecord) {
    setExpanded((prev) => ({ ...prev, [record.id]: { loading: true, items: [], error: null } }));
    try {
      const findings = await fetchReviewFindings(record.id);
      setExpanded((prev) => ({ ...prev, [record.id]: { loading: false, items: findings, error: null } }));
    } catch (caught) {
      setExpanded((prev) => ({
        ...prev,
        [record.id]: {
          loading: false,
          items: [],
          error: caught instanceof Error ? caught.message : '加载问题失败',
        },
      }));
    }
  }

  const columns: ColumnsType<ReviewRecord> = [
    {
      title: 'Commit',
      key: 'commit',
      width: 300,
      render: (_, record) => (
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[13px] font-medium text-zinc-900">
              {record.commit_sha.slice(0, 7)}
            </span>
            <KindTag kind={record.review_kind} />
          </div>
          <div className="mt-0.5 truncate text-[11px] text-zinc-500">
            {record.source_branch}
            {record.base_sha ? (
              <Tooltip title={`diff 起点：${record.base_sha.slice(0, 7)}`}>
                <span className="ml-1 font-mono">← {record.base_sha.slice(0, 7)}</span>
              </Tooltip>
            ) : null}
          </div>
        </div>
      ),
    },
    {
      title: '项目',
      dataIndex: 'project_name',
      key: 'project_name',
      width: 160,
      ellipsis: true,
      render: (value: string | null) => value ?? '-',
    },
    {
      title: '状态',
      key: 'status',
      width: 200,
      render: (_, record) => (
        <Space size={4} wrap>
          <StatusDot tone={statusTone(record)} />
          <ReviewStatusTag status={record.status} hasBlocker={record.has_blocker} />
        </Space>
      ),
    },
    {
      title: '问题数',
      dataIndex: 'finding_count',
      key: 'finding_count',
      width: 90,
      sorter: true,
      render: (value: number) => (value > 0 ? <span className="font-medium">{value}</span> : 0),
    },
    {
      title: '引擎',
      dataIndex: 'engine_used',
      key: 'engine_used',
      width: 120,
      ellipsis: true,
      render: (value: string | null) => value ?? '-',
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 130,
      sorter: true,
      render: (value: string | undefined) =>
        value ? (
          <Tooltip title={new Date(value).toLocaleString()}>
            <span className="text-zinc-500">{relativeTime(value)}</span>
          </Tooltip>
        ) : (
          '-'
        ),
    },
  ];

  return (
    <Card
      title="Push/Commit 审查"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={reload} />
        </Space>
      }
      styles={{ body: { paddingTop: 0 } }}
    >
      <div className="flex flex-wrap items-center gap-2 py-3">
        <Select
          aria-label="按项目筛选"
          placeholder="全部项目"
          allowClear
          showSearch
          optionFilterProp="label"
          style={{ minWidth: 180 }}
          value={filters.project_id || undefined}
          options={projects.map((project) => ({ value: project.id, label: project.name }))}
          onChange={(value) => setFilters({ project_id: value ?? '' })}
        />
        <Select
          aria-label="按状态筛选"
          placeholder="全部状态"
          allowClear
          style={{ minWidth: 140 }}
          value={filters.status || undefined}
          options={STATUS_OPTIONS}
          onChange={(value) => setFilters({ status: value ?? '' })}
        />
        <Select
          aria-label="按来源筛选"
          placeholder="全部来源"
          allowClear
          style={{ minWidth: 170 }}
          value={filters.review_kind || undefined}
          options={KIND_OPTIONS}
          onChange={(value) => setFilters({ review_kind: value ?? '' })}
        />
        <Input
          aria-label="按 commit SHA 搜索"
          placeholder="commit SHA（完整或前 7 位）"
          allowClear
          style={{ width: 220 }}
          value={shaInput}
          onChange={(event) => setShaInput(event.target.value)}
        />
      </div>

      {error ? <Alert type="error" showIcon message={error} className="mb-3" /> : null}

      <Table<ReviewRecord>
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={pagination}
        onChange={(_pagination, _filters, sorter) => {
          const field = Array.isArray(sorter) ? sorter[0] : sorter;
          if (field && 'field' in field) {
            setSort(String(field.field), (field.order ?? null) as 'ascend' | 'descend' | null);
          }
        }}
        expandable={{
          expandedRowRender: (record) => {
            const state = expanded[record.id];
            if (!state || state.loading) {
              return <div className="py-2 text-[12px] text-zinc-500">加载中…</div>;
            }
            if (state.error) {
              return <Alert type="error" showIcon message={state.error} />;
            }
            return (
              <div>
                <div className="pb-2">
                  <Button
                    size="small"
                    type="link"
                    className="!px-0"
                    onClick={() => setTraceReviewId(record.id)}
                  >
                    执行轨迹
                  </Button>
                  <span className="ml-2 text-[11px] text-zinc-400">
                    查看 agent 调查过程：每轮模型响应与工具调用参数/输出
                  </span>
                </div>
                {state.items.length === 0 ? (
                  <div className="py-2 text-[12px] text-zinc-500">暂无问题</div>
                ) : (
                  <div className="divide-y divide-zinc-100">
                    {state.items.map((finding) => (
                      <div key={finding.id} className="flex items-start justify-between gap-3 py-2">
                        <div className="min-w-0 flex-1">
                          <div className="text-[13px] font-medium text-zinc-900">{finding.title}</div>
                          <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
                            {finding.file_path}:{finding.line_number ?? '-'} · {finding.rule_id}
                          </div>
                        </div>
                        <SeverityTag severity={finding.severity} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          },
          onExpand: (isExpanded, record) => {
            if (isExpanded && !expanded[record.id]) {
              void loadExpandedFindings(record);
            }
          },
        }}
        locale={{
          emptyText: '暂无 Push/Commit 审查 · 当 GitLab Push Hook 触发审查后，记录会显示在这里',
        }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">
        共 {total} 条 Push/Commit 审查（服务端分页，每页 {pagination.pageSize} 条）
      </div>
      <AgentTraceDrawer reviewId={traceReviewId} onClose={() => setTraceReviewId(null)} />
    </Card>
  );
}
