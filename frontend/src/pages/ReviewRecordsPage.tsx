/**
 * 「审查记录」页：服务端分页表格（全来源统一口径）。
 *
 * 此前该页只拉后端默认前 20 条且无分页 UI（「N 条历史审查」文案用 items.length
 * 冒充总量）。现在接 /api/reviews/records 的完整查询参数：
 * - project_id / status / mr_iid / review_kind / commit_sha 服务端过滤；
 * - created_at / finding_count 等列排序（映射后端 sort 的 `-` 前缀降序约定）；
 * - antd Table 分页器显示真实 total；
 * - 行展开懒加载该审查的 findings（fetchReviewFindings）+ Agent 执行轨迹。
 *
 * feat/unified-review-records：MR / push / commit 审查合并到本页统一展示
 * （原独立「Push/Commit 审查」页已下线）。来源筛选器默认「MR」保住高频
 * 使用场景（push 审查高频，混合时间线会被刷屏），可一键切「全部」看完整
 * 审查流水。首列按 review_kind 分支渲染：MR 行显示 MR 编号 + 分支对，
 * push/commit 行显示 commit 短 SHA + 来源徽章。搜索框智能识别：纯数字
 * 按 MR 号过滤，含十六进制字符按 commit SHA 精确过滤。
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
  /** 来源筛选：'' = 默认 MR（避免 push 高频刷屏），'all' = 全部，'mr'/'commit'/'push' = 单来源。 */
  review_kind: string;
};

const STATUS_OPTIONS = [
  { value: 'pending', label: '待处理' },
  { value: 'running', label: '审查中' },
  { value: 'done', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'engine_error', label: '引擎异常' },
];

const KIND_OPTIONS = [
  { value: 'mr', label: 'MR 审查' },
  { value: 'all', label: '全部来源' },
  { value: 'push', label: 'Push 审查' },
  { value: 'commit', label: 'Commit 审查' },
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

/** 来源徽章：MR 行不渲染（默认态避免视觉噪声），push/commit 行显式标注。 */
function KindTag({ kind }: { kind: string | null | undefined }) {
  if (kind === 'push') {
    return <Tag color="blue">Push</Tag>;
  }
  if (kind === 'commit') {
    return <Tag color="cyan">Commit</Tag>;
  }
  return null;
}

/** 搜索词智能识别：纯数字按 MR 号过滤，含十六进制字符按 commit SHA 精确过滤。 */
function smartSearchParams(query: string): { mr_iid?: string; commit_sha?: string } {
  const trimmed = query.trim();
  if (!trimmed) {
    return {};
  }
  if (/^\d+$/.test(trimmed)) {
    return { mr_iid: trimmed };
  }
  if (/^[0-9a-f]{7,40}$/i.test(trimmed)) {
    return { commit_sha: trimmed };
  }
  // 非数字非 SHA（中文等）：不发起过滤，返回空对象。
  return {};
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
      ({ limit, offset, sort, filters: current }) => {
        // 来源筛选：默认 'mr'（高频场景防 push 刷屏）；'all' 不过滤；单来源传单值数组。
        const kind = current.review_kind || 'mr';
        const smart = smartSearchParams(current.mr_iid);
        return fetchReviewRecords({
          limit,
          offset,
          sort,
          project_id: current.project_id || undefined,
          status: current.status || undefined,
          review_kind: kind === 'all' ? undefined : [kind],
          mr_iid: smart.mr_iid,
          commit_sha: smart.commit_sha,
        });
      },
      {
        project_id: initialFilters?.project_id ?? '',
        status: initialFilters?.status ?? '',
        mr_iid: initialFilters?.mr_iid ?? '',
        // feat/unified-review-records：默认 MR 口径（防 push 高频刷屏），可切全部。
        review_kind: initialFilters?.review_kind ?? 'mr',
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
      title: '审查对象',
      key: 'subject',
      width: 300,
      render: (_, record) => {
        // feat/unified-review-records：按来源分支渲染。MR 行显示 MR 编号 + 分支对；
        // push/commit 行显示 commit 短 SHA + 来源徽章 + 单分支 + diff 起点。
        if (record.review_kind === 'push' || record.review_kind === 'commit') {
          return (
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
          );
        }
        return (
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
        );
      },
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
          aria-label="按来源筛选"
          placeholder="来源"
          style={{ minWidth: 130 }}
          value={filters.review_kind || 'mr'}
          options={KIND_OPTIONS}
          onChange={(value) => setFilters({ review_kind: value })}
        />
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
          aria-label="搜索审查对象"
          placeholder="MR 号（如 128）或 commit SHA（如 c0ffee0）"
          allowClear
          style={{ width: 260 }}
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
        locale={{
          emptyText:
            filters.review_kind === 'mr' || !filters.review_kind
              ? '暂无 MR 审查 · 当 GitLab MR 触发审查后，记录会显示在这里（切换上方「全部来源」可查看 push/commit 审查）'
              : '暂无审查记录 · 切换「来源」筛选或调整搜索条件试试',
        }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">
        共 {total} 条审查记录（服务端分页，每页 {pagination.pageSize} 条）
      </div>
      <AgentTraceDrawer reviewId={traceReviewId} onClose={() => setTraceReviewId(null)} />
    </Card>
  );
}
