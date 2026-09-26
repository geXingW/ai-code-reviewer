/**
 * 「审查记录」页：服务端分页表格。
 *
 * 此前该页只拉后端默认前 20 条且无分页 UI（「N 条历史审查」文案用 items.length
 * 冒充总量）。现在接 /api/reviews/records 的完整查询参数：
 * - project_id / status / mr_iid 服务端过滤；
 * - created_at / finding_count 等列排序（映射后端 sort 的 `-` 前缀降序约定）；
 * - antd Table 分页器显示真实 total；
 * - 行展开懒加载该 MR 的 findings（fetchReviewFindings）。
 */

import { useEffect, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import { App as AntApp, Alert, Button, Card, Input, Select, Space, Table, Tooltip } from 'antd';
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
import {
  LifecycleEventTag,
  ReviewModeTag,
  ReviewStatusTag,
  SeverityTag,
  StatusDot,
} from '../components/entityTags';

export interface ReviewRecordsPageProps {
  initialFilters?: Record<string, string>;
}

type Filters = {
  project_id: string;
  status: string;
  mr_iid: string;
};

const STATUS_OPTIONS = [
  { value: 'pending', label: '待处理' },
  { value: 'running', label: '审查中' },
  { value: 'done', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'engine_error', label: '引擎异常' },
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

export function ReviewRecordsPage({ initialFilters }: ReviewRecordsPageProps) {
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
          mr_iid: current.mr_iid || undefined,
        }),
      {
        project_id: initialFilters?.project_id ?? '',
        status: initialFilters?.status ?? '',
        mr_iid: initialFilters?.mr_iid ?? '',
      },
    );

  const [mrIidInput, setMrIidInput] = useState(initialFilters?.mr_iid ?? '');
  const debouncedMrIid = useDebouncedValue(mrIidInput, 300);
  useEffect(() => {
    if ((filters.mr_iid ?? '') !== debouncedMrIid) {
      setFilters({ mr_iid: debouncedMrIid });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedMrIid]);

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
      title: 'MR',
      key: 'mr',
      width: 260,
      render: (_, record) => (
        <div className="min-w-0">
          <div className="truncate text-[13px] font-medium text-zinc-900">
            {record.mr_iid ? `MR !${record.mr_iid}` : 'Commit 审查'}
            <span className="ml-2 font-normal text-zinc-500">
              {record.source_branch} → {record.target_branch}
            </span>
          </div>
          <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
            {record.commit_sha.slice(0, 7)}
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
      width: 220,
      render: (_, record) => (
        <Space size={4} wrap>
          <StatusDot tone={statusTone(record)} />
          <ReviewStatusTag status={record.status} hasBlocker={record.has_blocker} />
          {record.lifecycle_event ? (
            <LifecycleEventTag event={record.lifecycle_event} />
          ) : (
            <ReviewModeTag mode={record.review_mode} baseSha={record.base_sha} />
          )}
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
      title="审查记录"
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
        <Input
          aria-label="按 MR 号搜索"
          placeholder="MR 号，如 128"
          allowClear
          style={{ width: 160 }}
          value={mrIidInput}
          onChange={(event) => setMrIidInput(event.target.value)}
        />
      </div>

      {error ? (
        <Alert type="error" showIcon message={error} className="mb-3" />
      ) : null}

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
            if (record.lifecycle_event) {
              return <div className="py-2 text-[12px] text-zinc-500">MR 生命周期事件，未产生新的审查内容</div>;
            }
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
        locale={{ emptyText: '暂无审查记录 · 当 GitLab MR 触发审查后，记录会显示在这里' }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">
        共 {total} 条历史审查（服务端分页，每页 {pagination.pageSize} 条）
      </div>
      <AgentTraceDrawer reviewId={traceReviewId} onClose={() => setTraceReviewId(null)} />
    </Card>
  );
}
