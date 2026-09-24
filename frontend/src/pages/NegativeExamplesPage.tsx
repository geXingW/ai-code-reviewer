/**
 * 「负样本库」页：已批准负样本的服务端分页表格。
 *
 * 此前的「关键字搜索」只过滤已加载的前 20 条（后端无对应查询参数，属于假
 * 搜索）；现在移除关键字框，规则 / 项目筛选直连服务端 rule_id / project_id
 * 参数，分页接真实 total。
 */

import { useEffect, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Select, Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  fetchNegativeExamples,
  fetchProjectsAll,
  fetchRules,
  type NegativeExample,
  type ProjectConfig,
  type RuleConfig,
} from '../api';
import { usePagedList } from '../hooks/usePagedList';
import { relativeTime } from '../lib/format';

export interface NegativeExamplesPageProps {
  initialFilters?: Record<string, string>;
}

type Filters = {
  rule_id: string;
  project_id: string;
};

export function NegativeExamplesPage({ initialFilters }: NegativeExamplesPageProps) {
  const [rules, setRules] = useState<RuleConfig[]>([]);
  const [projects, setProjects] = useState<ProjectConfig[]>([]);

  useEffect(() => {
    let active = true;
    fetchRules()
      .then((page) => {
        if (active) setRules(page.items);
      })
      .catch(() => {});
    fetchProjectsAll()
      .then((page) => {
        if (active) setProjects(page.items);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  const { items, total, loading, error, filters, setFilters, reload, pagination } =
    usePagedList<NegativeExample, Filters>(
      ({ limit, offset, sort, filters: current }) =>
        fetchNegativeExamples({
          limit,
          offset,
          sort,
          rule_id: current.rule_id || undefined,
          project_id: current.project_id || undefined,
        }),
      {
        rule_id: initialFilters?.rule_id ?? '',
        project_id: initialFilters?.project_id ?? '',
      },
    );

  const columns: ColumnsType<NegativeExample> = [
    {
      title: '规则',
      dataIndex: 'rule_id',
      key: 'rule_id',
      width: 220,
      ellipsis: true,
      render: (value: string) => <span className="font-mono text-[12px] text-zinc-700">{value}</span>,
    },
    {
      title: '代码片段',
      dataIndex: 'code_snippet',
      key: 'code_snippet',
      render: (value: string) => (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-zinc-50 p-2 font-mono text-[12px] text-zinc-700">
          {value}
        </pre>
      ),
    },
    {
      title: '说明',
      dataIndex: 'explanation',
      key: 'explanation',
      width: 280,
      render: (value: string | null) => (
        <span className="whitespace-pre-wrap text-[12px] text-zinc-600">{value ?? '—'}</span>
      ),
    },
    {
      title: '批准人',
      dataIndex: 'approved_by',
      key: 'approved_by',
      width: 110,
      ellipsis: true,
      render: (value: string | null) => value ?? '—',
    },
    {
      title: '批准时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 120,
      render: (value: string | undefined) => (
        <span className="text-zinc-500">{relativeTime(value)}</span>
      ),
    },
  ];

  return (
    <Card
      title="负样本库"
      extra={<Button icon={<ReloadOutlined />} onClick={reload} />}
      styles={{ body: { paddingTop: 0 } }}
    >
      <div className="flex flex-wrap items-center gap-2 py-3">
        <Select
          aria-label="按规则筛选"
          placeholder="全部规则"
          allowClear
          showSearch
          optionFilterProp="label"
          style={{ minWidth: 220 }}
          value={filters.rule_id || undefined}
          options={rules.map((rule) => ({ value: rule.rule_id, label: `${rule.rule_id} · ${rule.title}` }))}
          onChange={(value) => setFilters({ rule_id: value ?? '' })}
        />
        <Select
          aria-label="按项目筛选"
          placeholder="全部项目"
          allowClear
          showSearch
          optionFilterProp="label"
          style={{ minWidth: 200 }}
          value={filters.project_id || undefined}
          options={projects.map((project) => ({ value: project.id, label: project.name }))}
          onChange={(value) => setFilters({ project_id: value ?? '' })}
        />
      </div>

      {error ? (
        <Alert type="error" showIcon message={error} className="mb-3" />
      ) : null}

      <Table<NegativeExample>
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={pagination}
        locale={{ emptyText: '暂无负样本 · 确认误报后代码片段会沉淀到这里' }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">共 {total} 条已批准</div>
    </Card>
  );
}
