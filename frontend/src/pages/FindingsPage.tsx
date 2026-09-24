/**
 * 「问题与误报」页：服务端分页表格。
 *
 * 此前的筛选（误报状态 / 严重度 / 关键字）全是前端 filter，只作用于已加载的
 * 前 20 条——数据超过一页时搜索结果是错的。现在全部直连后端参数：
 * - fp_status / severity 精确过滤；
 * - file_path 关键字为 ilike 模糊匹配（防抖 300ms）；
 * - severity / created_at 列排序映射后端 sort。
 *
 * 行操作：标记已解决 / 标记误报（弹窗提交后刷新当前页）。
 */

import { useEffect, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import { App as AntApp, Alert, Button, Card, Input, Select, Space, Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  fetchFindings,
  type FindingRecord,
  markFalsePositive,
  resolveFinding,
} from '../api';
import { usePagedList, useDebouncedValue } from '../hooks/usePagedList';
import { relativeTime } from '../lib/format';
import { FindingStatusTag, FpStatusTag, SeverityTag } from '../components/entityTags';
import { MarkFalsePositiveDialog } from '../components/dialogs/MarkFalsePositiveDialog';
import { ResolveDialog } from '../components/dialogs/ResolveDialog';
import { getStoredAdminUsername, isAuthRequiredError } from '../api';

export interface FindingsPageProps {
  initialFilters?: Record<string, string>;
}

type Filters = {
  fp_status: string;
  severity: string;
  file_path: string;
};

const FP_STATUS_OPTIONS = [
  { value: 'NONE', label: '未处理' },
  { value: 'PENDING', label: '误报待审' },
  { value: 'CONFIRMED', label: '已确认误报' },
  { value: 'REJECTED', label: '误报驳回' },
];

const SEVERITY_OPTIONS = [
  { value: 'BLOCKER', label: '🔴 BLOCKER' },
  { value: 'WARNING', label: '🟡 WARNING' },
  { value: 'INFO', label: '🔵 INFO' },
];

export function FindingsPage({ initialFilters }: FindingsPageProps) {
  const { message } = AntApp.useApp();
  const [markDialogFinding, setMarkDialogFinding] = useState<FindingRecord | null>(null);
  const [resolveDialogFinding, setResolveDialogFinding] = useState<FindingRecord | null>(null);

  const { items, total, loading, error, filters, setFilters, setSort, reload, pagination } =
    usePagedList<FindingRecord, Filters>(
      ({ limit, offset, sort, filters: current }) =>
        fetchFindings({
          limit,
          offset,
          sort,
          fp_status: current.fp_status || undefined,
          severity: current.severity || undefined,
          file_path: current.file_path || undefined,
        }),
      {
        fp_status: initialFilters?.fp_status ?? '',
        severity: initialFilters?.severity ?? '',
        file_path: initialFilters?.file_path ?? '',
      },
    );

  const [pathInput, setPathInput] = useState(initialFilters?.file_path ?? '');
  const debouncedPath = useDebouncedValue(pathInput, 300);
  useEffect(() => {
    if ((filters.file_path ?? '') !== debouncedPath) {
      setFilters({ file_path: debouncedPath });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedPath]);

  async function handleSubmitMark(finding: FindingRecord, payload: { marked_by: string; reason: string }) {
    try {
      await markFalsePositive(finding.id, payload);
      message.success('问题已标记为待确认误报。');
      setMarkDialogFinding(null);
      reload();
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '提交失败');
      }
      throw caught;
    }
  }

  async function handleSubmitResolve(finding: FindingRecord, payload: { resolved_by: string; reason: string }) {
    try {
      await resolveFinding(finding.id, payload);
      message.success('问题已标记为已解决，MR 阻断状态已更新。');
      setResolveDialogFinding(null);
      reload();
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '提交失败');
      }
      throw caught;
    }
  }

  const defaultOperator = getStoredAdminUsername() || 'admin';

  const columns: ColumnsType<FindingRecord> = [
    {
      title: '问题',
      dataIndex: 'title',
      key: 'title',
      width: 300,
      render: (_, record) => (
        <Tooltip title={record.description ?? undefined} placement="topLeft">
          <div className="min-w-0">
            <div className="truncate text-[13px] font-medium text-zinc-900">{record.title}</div>
            <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
              {record.file_path}:{record.line_number ?? '-'} · {record.rule_id}
            </div>
          </div>
        </Tooltip>
      ),
    },
    {
      title: '位置',
      key: 'location',
      width: 220,
      ellipsis: true,
      render: (_, record) => (
        <div className="min-w-0 text-[12px]">
          <div className="truncate text-zinc-700">{record.project_name ?? '未知项目'}</div>
          <div className="truncate text-zinc-500">
            {record.mr_iid ? `MR !${record.mr_iid}` : 'MR !-'}
            {record.review_created_at ? ` · ${relativeTime(record.review_created_at)}` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '严重度',
      dataIndex: 'severity',
      key: 'severity',
      width: 110,
      sorter: true,
      render: (value: string) => <SeverityTag severity={value} />,
    },
    {
      title: '状态',
      key: 'status_tags',
      width: 200,
      render: (_, record) => (
        <Space size={4} wrap>
          <FindingStatusTag status={record.status} />
          <FpStatusTag status={record.fp_status} />
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 190,
      render: (_, record) => {
        const canMark = record.fp_status === 'NONE';
        return (
          <Space size={0}>
            <Button
              type="link"
              size="small"
              disabled={record.status === 'resolved'}
              title={record.status === 'resolved' ? '该问题已标记为已解决' : '直接标记为已解决，不进入误报流程'}
              onClick={() => setResolveDialogFinding(record)}
            >
              标记已解决
            </Button>
            <Button
              type="link"
              size="small"
              disabled={!canMark}
              title={
                record.fp_marked_by
                  ? `已由 ${record.fp_marked_by} 于 ${relativeTime(record.fp_marked_at ?? undefined)} 标记`
                  : undefined
              }
              onClick={() => setMarkDialogFinding(record)}
            >
              {canMark ? '标记误报' : record.fp_status === 'PENDING' ? '已提交' : '已处理'}
            </Button>
          </Space>
        );
      },
    },
  ];

  return (
    <Card
      title="问题与误报"
      extra={
        <Button icon={<ReloadOutlined />} onClick={reload} />
      }
      styles={{ body: { paddingTop: 0 } }}
    >
      <div className="flex flex-wrap items-center gap-2 py-3">
        <Select
          aria-label="按误报状态筛选"
          placeholder="误报状态：全部"
          allowClear
          style={{ minWidth: 150 }}
          value={filters.fp_status || undefined}
          options={FP_STATUS_OPTIONS}
          onChange={(value) => setFilters({ fp_status: value ?? '' })}
        />
        <Select
          aria-label="按严重度筛选"
          placeholder="严重度：全部"
          allowClear
          style={{ minWidth: 150 }}
          value={filters.severity || undefined}
          options={SEVERITY_OPTIONS}
          onChange={(value) => setFilters({ severity: value ?? '' })}
        />
        <Input
          aria-label="按文件路径搜索"
          placeholder="搜索文件路径，如 api/user.py"
          allowClear
          style={{ width: 260 }}
          prefix="🔍"
          value={pathInput}
          onChange={(event) => setPathInput(event.target.value)}
        />
      </div>

      {error ? (
        <Alert type="error" showIcon message={error} className="mb-3" />
      ) : null}

      <Table<FindingRecord>
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
        locale={{ emptyText: '暂无问题记录' }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">
        共 {total} 条问题记录（筛选与搜索均为服务端查询）
      </div>

      <MarkFalsePositiveDialog
        open={markDialogFinding !== null}
        finding={markDialogFinding}
        defaultMarkedBy={defaultOperator}
        onCancel={() => setMarkDialogFinding(null)}
        onSubmit={(payload) =>
          markDialogFinding
            ? handleSubmitMark(markDialogFinding, payload)
            : Promise.resolve()
        }
      />
      <ResolveDialog
        open={resolveDialogFinding !== null}
        finding={resolveDialogFinding}
        defaultResolvedBy={defaultOperator}
        onCancel={() => setResolveDialogFinding(null)}
        onSubmit={(payload) =>
          resolveDialogFinding
            ? handleSubmitResolve(resolveDialogFinding, payload)
            : Promise.resolve()
        }
      />
    </Card>
  );
}
