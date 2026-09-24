/**
 * 「引擎配置」页：服务端分页表格 + q 搜索 + enabled 过滤。
 *
 * 此前只显示后端默认前 20 条（无分页、无搜索）；现在直连
 * /api/engines/configs 的 q / enabled / limit / offset / sort 参数。
 * 引擎运行时健康状态来自 /api/engines（内存注册表，全量返回）。
 */

import { useEffect, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Input, Select, Space, Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  fetchEngineConfigs,
  fetchEngines,
  type EngineConfig,
  type EngineSummary,
} from '../api';
import { usePagedList, useDebouncedValue } from '../hooks/usePagedList';
import { EnabledTag, StatusDot } from '../components/entityTags';

type Filters = {
  q: string;
  enabled: string;
};

export function EnginesPage() {
  const [engines, setEngines] = useState<EngineSummary[]>([]);

  useEffect(() => {
    let active = true;
    fetchEngines()
      .then((list) => {
        if (active) setEngines(list);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  const { items, total, loading, error, filters, setFilters, setSort, reload, pagination } =
    usePagedList<EngineConfig, Filters>(
      ({ limit, offset, sort, filters: current }) =>
        fetchEngineConfigs({
          limit,
          offset,
          sort,
          q: current.q || undefined,
          enabled: current.enabled === '' ? undefined : current.enabled === 'true',
        }),
      { q: '', enabled: '' },
    );

  const [qInput, setQInput] = useState('');
  const debouncedQ = useDebouncedValue(qInput, 300);
  useEffect(() => {
    if ((filters.q ?? '') !== debouncedQ) {
      setFilters({ q: debouncedQ });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);

  function engineHealth(name: string): EngineSummary | undefined {
    return engines.find((engine) => engine.name === name);
  }

  const columns: ColumnsType<EngineConfig> = [
    {
      title: '引擎',
      dataIndex: 'name',
      key: 'name',
      width: 220,
      sorter: true,
      render: (value: string, record) => {
        const runtime = engineHealth(value);
        return (
          <div className="flex items-center gap-2">
            <StatusDot
              tone={
                !runtime ? 'idle' : runtime.healthy ? 'ok' : 'bad'
              }
            />
            <div className="min-w-0">
              <div className="truncate text-[13px] font-medium text-zinc-900">{value}</div>
              <div className="truncate text-[11px] text-zinc-500">
                {record.description ?? '暂无描述'}
              </div>
            </div>
          </div>
        );
      },
    },
    {
      title: '运行状态',
      key: 'health',
      width: 180,
      render: (_, record) => {
        const runtime = engineHealth(record.name);
        if (!runtime) {
          return <span className="text-[12px] text-zinc-400">未注册运行时</span>;
        }
        return (
          <Tooltip title={runtime.health_status}>
            <Space size={6}>
              <span className="text-[12px] text-zinc-600">{runtime.health_status}</span>
              {runtime.supports_feedback ? (
                <span className="rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 text-[10px] text-zinc-600">
                  支持反馈
                </span>
              ) : null}
            </Space>
          </Tooltip>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 90,
      sorter: true,
      render: (value: boolean) => <EnabledTag enabled={value} />,
    },
  ];

  return (
    <Card
      title="引擎配置"
      extra={<Button icon={<ReloadOutlined />} onClick={reload} />}
      styles={{ body: { paddingTop: 0 } }}
    >
      <div className="flex flex-wrap items-center gap-2 py-3">
        <Input
          aria-label="搜索引擎"
          placeholder="搜索引擎名称"
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

      <Table<EngineConfig>
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
        locale={{ emptyText: '暂无引擎配置 · 引擎配置会在首次启动时自动创建' }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">共 {total} 个引擎</div>
    </Card>
  );
}
