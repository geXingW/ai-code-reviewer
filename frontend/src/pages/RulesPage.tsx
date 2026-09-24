/**
 * 「审查规则」页：服务端分页表格 + q 搜索 + enabled 过滤。
 *
 * 此前规则页是全量循环拉取后本地展示（无分页、无搜索）；现在列表走
 * fetchRulesPage 服务端分页（q 匹配 rule_id / title），新增/编辑走
 * RuleDialog，删除带 Popconfirm。规则全量接口仍保留给规则关联面板。
 */

import { useEffect, useState } from 'react';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { App as AntApp, Alert, Button, Card, Input, Popconfirm, Select, Space, Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  createRule,
  deleteRule,
  fetchRulesPage,
  isAuthRequiredError,
  updateRule,
  type RuleConfig,
} from '../api';
import { usePagedList, useDebouncedValue } from '../hooks/usePagedList';
import { truncate } from '../lib/format';
import { EnabledTag, SeverityTag } from '../components/entityTags';
import { RuleDialog } from '../components/dialogs/RuleDialog';

type RuleDialogState = { mode: 'create' } | { mode: 'edit'; data: RuleConfig } | null;

type Filters = {
  q: string;
  enabled: string;
};

export function RulesPage({ initialFilters }: { initialFilters?: Record<string, string> }) {
  const { message } = AntApp.useApp();
  const [dialog, setDialog] = useState<RuleDialogState>(null);

  const { items, total, loading, error, filters, setFilters, setSort, reload, pagination } =
    usePagedList<RuleConfig, Filters>(
      ({ limit, offset, sort, filters: current }) =>
        fetchRulesPage({
          limit,
          offset,
          sort,
          q: current.q || undefined,
          enabled: current.enabled === '' ? undefined : current.enabled === 'true',
        }),
      {
        q: initialFilters?.q ?? '',
        enabled: '',
      },
    );

  const [qInput, setQInput] = useState(initialFilters?.q ?? '');
  const debouncedQ = useDebouncedValue(qInput, 300);
  useEffect(() => {
    if ((filters.q ?? '') !== debouncedQ) {
      setFilters({ q: debouncedQ });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);

  async function handleDelete(rule: RuleConfig) {
    try {
      await deleteRule(rule.id);
      message.success('审查规则已删除。');
      reload();
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '删除失败');
      }
    }
  }

  const columns: ColumnsType<RuleConfig> = [
    {
      title: '规则',
      key: 'rule',
      render: (_, record) => (
        <div className="min-w-0">
          <div className="truncate text-[13px]">
            <span className="font-mono">{record.rule_id}</span>
            <span className="ml-1.5 font-normal text-zinc-600">{record.title}</span>
          </div>
          <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
            {truncate(record.prompt_snippet, 60)}
          </div>
        </div>
      ),
    },
    {
      title: '标签',
      key: 'tags',
      width: 200,
      render: (_, record) =>
        record.tags.length > 0 ? (
          <Space size={4} wrap>
            {record.tags.map((tag) => (
              <span
                key={tag}
                className="rounded-full bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700"
              >
                {tag}
              </span>
            ))}
          </Space>
        ) : (
          '-'
        ),
    },
    {
      title: '严重度',
      dataIndex: 'severity_default',
      key: 'severity_default',
      width: 110,
      render: (value: string) => <SeverityTag severity={value} />,
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 90,
      sorter: true,
      render: (value: boolean) => <EnabledTag enabled={value} />,
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => setDialog({ mode: 'edit', data: record })}>
            编辑
          </Button>
          <Popconfirm
            title={`确定删除规则「${record.rule_id}」？`}
            description="该操作不可撤销。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => void handleDelete(record)}
          >
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="审查规则"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={reload} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setDialog({ mode: 'create' })}>
            新增规则
          </Button>
        </Space>
      }
      styles={{ body: { paddingTop: 0 } }}
    >
      <div className="flex flex-wrap items-center gap-2 py-3">
        <Input
          aria-label="搜索规则"
          placeholder="搜索 rule_id / 标题"
          allowClear
          style={{ width: 240 }}
          value={qInput}
          onChange={(event) => setQInput(event.target.value)}
        />
        <Select
          aria-label="按状态筛选"
          placeholder="全部状态"
          allowClear
          style={{ minWidth: 120 }}
          value={filters.enabled === '' ? undefined : filters.enabled}
          options={[
            { value: 'true', label: '已启用' },
            { value: 'false', label: '已停用' },
          ]}
          onChange={(value) => setFilters({ enabled: value ?? '' })}
        />
      </div>

      {error ? (
        <Alert type="error" showIcon message={error} className="mb-3" />
      ) : null}

      <Table<RuleConfig>
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
        locale={{ emptyText: '暂无审查规则' }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">
        共 {total} 条规则（搜索为服务端查询）
      </div>

      <RuleDialog
        open={dialog !== null}
        initialData={dialog?.mode === 'edit' ? dialog.data : null}
        onCancel={() => setDialog(null)}
        onSubmit={async (payload) => {
          try {
            if (dialog?.mode === 'create') {
              // rule_id 可选：留空则由后端从标题自动生成 slug。
              await createRule({ ...payload, rule_id: payload.rule_id.trim() || undefined });
              message.success('审查规则已创建。');
            } else if (dialog?.mode === 'edit') {
              // rule_id 只读，不随更新提交，避免误改业务标识。
              await updateRule(dialog.data.id, {
                title: payload.title,
                prompt_snippet: payload.prompt_snippet,
                severity_default: payload.severity_default,
                tags: payload.tags,
                enabled: payload.enabled,
              });
              message.success('审查规则已更新。');
            }
            setDialog(null);
            reload();
          } catch (caught) {
            if (!isAuthRequiredError(caught)) {
              message.error(caught instanceof Error ? caught.message : '提交失败');
            }
            throw caught;
          }
        }}
      />
    </Card>
  );
}
