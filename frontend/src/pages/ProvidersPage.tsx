/**
 * 「模型供应商」页：服务端分页表格 + q 搜索 + enabled 过滤。
 *
 * 此前只显示后端默认前 20 条（无分页、无搜索）；现在 q 直连后端
 * name ilike，新增/编辑走 ProviderDialog（提交后刷新当前页）。
 */

import { useEffect, useState } from 'react';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { App as AntApp, Alert, Button, Card, Input, Popconfirm, Select, Space, Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  createProvider,
  deleteProvider,
  fetchProviders,
  isAuthRequiredError,
  updateProvider,
  type ProviderConfig,
} from '../api';
import { usePagedList, useDebouncedValue } from '../hooks/usePagedList';
import { EnabledTag } from '../components/entityTags';
import { ProviderDialog } from '../components/dialogs/ProviderDialog';

type ProviderDialogState = { mode: 'create' } | { mode: 'edit'; data: ProviderConfig } | null;

type Filters = {
  q: string;
  enabled: string;
};

export function ProvidersPage({ initialFilters }: { initialFilters?: Record<string, string> }) {
  const { message } = AntApp.useApp();
  const [dialog, setDialog] = useState<ProviderDialogState>(null);

  const { items, total, loading, error, filters, setFilters, setSort, reload, pagination } =
    usePagedList<ProviderConfig, Filters>(
      ({ limit, offset, sort, filters: current }) =>
        fetchProviders({
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

  async function handleDelete(provider: ProviderConfig) {
    try {
      await deleteProvider(provider.id);
      message.success('模型供应商已删除。');
      reload();
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '删除失败');
      }
    }
  }

  const columns: ColumnsType<ProviderConfig> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      sorter: true,
      render: (value: string) => <span className="font-medium text-zinc-900">{value}</span>,
    },
    {
      title: '协议 / 模型',
      key: 'protocol',
      width: 220,
      render: (_, record) => (
        <span className="font-mono text-[12px] text-zinc-600">
          {record.protocol} · {record.model}
        </span>
      ),
    },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      key: 'base_url',
      ellipsis: true,
      render: (value: string) => (
        <Tooltip title={value}>
          <span className="font-mono text-[12px] text-zinc-500">
            {value.replace(/^https?:\/\//, '').replace(/\/$/, '')}
          </span>
        </Tooltip>
      ),
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
            title={`确定删除供应商「${record.name}」？`}
            description="删除后使用该供应商的项目将无法发起审查。"
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
      title="模型供应商"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={reload} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setDialog({ mode: 'create' })}>
            新增供应商
          </Button>
        </Space>
      }
      styles={{ body: { paddingTop: 0 } }}
    >
      <div className="flex flex-wrap items-center gap-2 py-3">
        <Input
          aria-label="搜索供应商"
          placeholder="搜索供应商名称"
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

      <Table<ProviderConfig>
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
        locale={{ emptyText: '暂无模型供应商' }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">
        共 {total} 个供应商（搜索为服务端查询）
      </div>

      <ProviderDialog
        open={dialog !== null}
        initialData={dialog?.mode === 'edit' ? dialog.data : null}
        onCancel={() => setDialog(null)}
        onSubmit={async (payload) => {
          try {
            if (dialog?.mode === 'create') {
              await createProvider(payload);
              message.success('模型供应商已创建。');
            } else if (dialog?.mode === 'edit') {
              await updateProvider(dialog.data.id, payload);
              message.success('模型供应商已更新。');
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
