/**
 * 「误报队列」页：待审核误报的服务端分页表格。
 *
 * 此前只显示后端默认前 20 条且文案冒充总量；现在分页接 total，
 * 确认 / 驳回走 ReviewFalsePositiveDialog（提交后刷新当前页）。
 */

import { useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import { App as AntApp, Alert, Button, Card, Space, Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  confirmFalsePositive,
  fetchPendingFalsePositives,
  type FindingRecord,
  isAuthRequiredError,
  rejectFalsePositive,
} from '../api';
import { usePagedList } from '../hooks/usePagedList';
import { relativeTime } from '../lib/format';
import { SeverityTag } from '../components/entityTags';
import { ReviewFalsePositiveDialog } from '../components/dialogs/ReviewFalsePositiveDialog';
import { getStoredAdminUsername } from '../api';

export function FalsePositivesPage() {
  const { message } = AntApp.useApp();
  const [reviewDialog, setReviewDialog] = useState<{
    finding: FindingRecord;
    action: 'confirm' | 'reject';
  } | null>(null);

  const { items, total, loading, error, reload, pagination } =
    usePagedList<FindingRecord, Record<string, never>>(
      ({ limit, offset }) => fetchPendingFalsePositives({ limit, offset }),
      {},
    );

  async function handleSubmitReview(
    finding: FindingRecord,
    action: 'confirm' | 'reject',
    payload: { reviewed_by: string; note: string },
  ) {
    const apiPayload = { reviewed_by: payload.reviewed_by, note: payload.note || undefined };
    try {
      if (action === 'confirm') {
        await confirmFalsePositive(finding.id, apiPayload);
        message.success('误报已确认并沉淀为负例。');
      } else {
        await rejectFalsePositive(finding.id, apiPayload);
        message.success('误报申请已驳回。');
      }
      setReviewDialog(null);
      reload();
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '提交失败');
      }
      throw caught;
    }
  }

  const columns: ColumnsType<FindingRecord> = [
    {
      title: '问题',
      dataIndex: 'title',
      key: 'title',
      width: 280,
      render: (_, record) => (
        <div className="min-w-0">
          <div className="truncate text-[13px] font-medium text-zinc-900">{record.title}</div>
          <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
            {record.file_path}:{record.line_number ?? '-'} · {record.rule_id}
          </div>
        </div>
      ),
    },
    {
      title: '位置',
      key: 'location',
      width: 180,
      ellipsis: true,
      render: (_, record) => (
        <div className="text-[12px] text-zinc-600">
          {record.project_name ?? '未知项目'}
          <span className="ml-1 font-mono text-zinc-500">
            {record.mr_iid ? `MR !${record.mr_iid}` : 'MR !-'}
          </span>
        </div>
      ),
    },
    {
      title: '提交信息',
      key: 'mark_info',
      width: 320,
      render: (_, record) => (
        <div className="min-w-0 text-[12px]">
          <div className="truncate text-zinc-600">
            提交人 <span className="text-zinc-800">{record.fp_marked_by ?? '未知'}</span>
            {record.fp_marked_at ? ` · ${relativeTime(record.fp_marked_at)}` : ''}
          </div>
          <Tooltip title={record.fp_marked_reason ?? '（未填写）'} placement="topLeft">
            <div className="mt-0.5 line-clamp-2 whitespace-pre-wrap break-words text-zinc-500">
              原因：{record.fp_marked_reason ?? '（未填写）'}
            </div>
          </Tooltip>
        </div>
      ),
    },
    {
      title: '严重度',
      dataIndex: 'severity',
      key: 'severity',
      width: 100,
      render: (value: string) => <SeverityTag severity={value} />,
    },
    {
      title: '操作',
      key: 'actions',
      width: 170,
      render: (_, record) => (
        <Space>
          <Button type="primary" size="small" onClick={() => setReviewDialog({ finding: record, action: 'confirm' })}>
            确认误报
          </Button>
          <Button size="small" onClick={() => setReviewDialog({ finding: record, action: 'reject' })}>
            驳回
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="误报队列"
      extra={<Button icon={<ReloadOutlined />} onClick={reload} />}
      styles={{ body: { paddingTop: 0 } }}
    >
      {error ? (
        <Alert type="error" showIcon message={error} className="mb-3" />
      ) : null}

      <Table<FindingRecord>
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={pagination}
        locale={{ emptyText: '暂无待确认误报 · 开发者标记的误报会进入此队列' }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">共 {total} 条待审核</div>

      <ReviewFalsePositiveDialog
        open={reviewDialog !== null}
        finding={reviewDialog?.finding ?? null}
        action={reviewDialog?.action ?? 'confirm'}
        defaultReviewedBy={getStoredAdminUsername() || 'admin'}
        onCancel={() => setReviewDialog(null)}
        onSubmit={(payload) =>
          reviewDialog
            ? handleSubmitReview(reviewDialog.finding, reviewDialog.action, payload)
            : Promise.resolve()
        }
      />
    </Card>
  );
}
