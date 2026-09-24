/**
 * 「GitLab 项目」页：服务端分页表格 + q 搜索 + enabled 过滤。
 *
 * 行展开显示阻断策略编辑器（BlockPolicyEditor，拖拽调优先级）；
 * 新增 / 编辑走 ProjectDialog，负样本提示词走 NegativePromptDialog。
 * 弹窗所需的引擎 / 供应商 / 规则全量选项在页内拉取。
 */

import { useEffect, useState } from 'react';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { App as AntApp, Alert, Button, Card, Input, Popconfirm, Select, Space, Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  createProject,
  deleteProject,
  fetchEngineConfigs,
  fetchProjects,
  fetchProvidersAll,
  fetchRules,
  isAuthRequiredError,
  updateProject,
  type BlockPolicyPayload,
  type EngineConfig,
  type ProjectConfig,
  type ProviderConfig,
  type RuleConfig,
} from '../api';
import { usePagedList, useDebouncedValue } from '../hooks/usePagedList';
import { EnabledTag } from '../components/entityTags';
import { BlockPolicyEditor } from '../components/BlockPolicyEditor';
import { ProjectDialog } from '../components/dialogs/ProjectDialog';
import { NegativePromptDialog } from '../components/dialogs/NegativePromptDialog';

type ProjectDialogState = { mode: 'create' } | { mode: 'edit'; data: ProjectConfig } | null;

type Filters = {
  q: string;
  enabled: string;
};

export function ProjectsPage({ initialFilters }: { initialFilters?: Record<string, string> }) {
  const { message } = AntApp.useApp();
  const [dialog, setDialog] = useState<ProjectDialogState>(null);
  const [negativePromptFor, setNegativePromptFor] = useState<ProjectConfig | null>(null);
  const [engines, setEngines] = useState<EngineConfig[]>([]);
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [rules, setRules] = useState<RuleConfig[]>([]);

  useEffect(() => {
    let active = true;
    // 弹窗选项数据：引擎配置（上限 100，够用）、供应商与规则拉全量。
    fetchEngineConfigs({ limit: 100 })
      .then((page) => {
        if (active) setEngines(page.items);
      })
      .catch(() => {});
    fetchProvidersAll()
      .then((page) => {
        if (active) setProviders(page.items);
      })
      .catch(() => {});
    fetchRules()
      .then((page) => {
        if (active) setRules(page.items);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  const { items, total, loading, error, filters, setFilters, setSort, reload, pagination } =
    usePagedList<ProjectConfig, Filters>(
      ({ limit, offset, sort, filters: current }) =>
        fetchProjects({
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

  async function handleSavePolicies(projectId: string, policies: BlockPolicyPayload[]) {
    try {
      await updateProject(projectId, { block_policies: policies });
      message.success('阻断策略已保存。');
      reload();
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '保存失败');
      }
    }
  }

  async function handleDelete(project: ProjectConfig) {
    try {
      await deleteProject(project.id);
      message.success('GitLab 项目已删除。');
      reload();
    } catch (caught) {
      if (!isAuthRequiredError(caught)) {
        message.error(caught instanceof Error ? caught.message : '删除失败');
      }
    }
  }

  const engineNameById = new Map(engines.map((engine) => [engine.id, engine.name]));
  const providerNameById = new Map(providers.map((provider) => [provider.id, provider.name]));

  const columns: ColumnsType<ProjectConfig> = [
    {
      title: '项目',
      key: 'name',
      sorter: true,
      render: (_, record) => (
        <div className="min-w-0">
          <div className="truncate text-[13px] font-medium text-zinc-900">{record.name}</div>
          <div className="truncate font-mono text-[11px] text-zinc-500">
            GitLab {record.gitlab_project_id}
          </div>
        </div>
      ),
    },
    {
      title: '引擎 / 供应商',
      key: 'engine',
      width: 220,
      ellipsis: true,
      render: (_, record) => (
        <span className="text-[12px] text-zinc-600">
          {record.engine_id ? engineNameById.get(record.engine_id) ?? '已指定' : '默认'} ·{' '}
          {record.provider_id ? providerNameById.get(record.provider_id) ?? '已指定' : '默认'}
        </span>
      ),
    },
    {
      title: '阻断级别',
      dataIndex: 'default_block_severity',
      key: 'default_block_severity',
      width: 100,
    },
    {
      title: '策略',
      key: 'policies',
      width: 80,
      render: (_, record) => <span className="text-zinc-600">{record.block_policies.length} 条</span>,
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
      width: 260,
      render: (_, record) => (
        <Space size={0} wrap>
          <Button type="link" size="small" onClick={() => setDialog({ mode: 'edit', data: record })}>
            编辑
          </Button>
          <Button type="link" size="small" onClick={() => setNegativePromptFor(record)}>
            负样本提示词
          </Button>
          <Popconfirm
            title={`确定删除项目「${record.name}」？`}
            description="删除后该项目的 MR 审查将无法使用。"
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
      title="GitLab 项目"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={reload} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setDialog({ mode: 'create' })}>
            新增项目
          </Button>
        </Space>
      }
      styles={{ body: { paddingTop: 0 } }}
    >
      <div className="flex flex-wrap items-center gap-2 py-3">
        <Input
          aria-label="搜索项目"
          placeholder="搜索项目名称 / GitLab ID"
          allowClear
          style={{ width: 260 }}
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

      <Table<ProjectConfig>
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
          expandedRowRender: (record) => (
            <div className="bg-zinc-50/60 px-3 pb-3">
              <div className="pt-2 text-[12px] font-medium text-zinc-500">
                阻断策略（按优先级排序，命中即阻断）
              </div>
              <BlockPolicyEditor projectId={record.id} policies={record.block_policies} onSave={handleSavePolicies} />
            </div>
          ),
        }}
        locale={{ emptyText: '暂无 GitLab 项目' }}
      />
      <div className="pt-1 text-[12px] text-zinc-400">共 {total} 个项目（搜索为服务端查询）</div>

      <ProjectDialog
        open={dialog !== null}
        initialData={dialog?.mode === 'edit' ? dialog.data : null}
        engineOptions={[
          { value: '', label: '不指定' },
          ...engines.map((engine) => ({ value: engine.id, label: engine.name })),
        ]}
        providerOptions={[
          { value: '', label: '（不选择，使用默认）' },
          ...providers.map((provider) => ({ value: provider.id, label: provider.name })),
        ]}
        rules={rules}
        onCancel={() => setDialog(null)}
        onSubmit={async (payload) => {
          try {
            if (dialog?.mode === 'create') {
              await createProject(payload);
              message.success('GitLab 项目已创建。');
            } else if (dialog?.mode === 'edit') {
              await updateProject(dialog.data.id, payload);
              message.success('GitLab 项目已更新。');
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

      {negativePromptFor ? (
        <NegativePromptDialog
          open
          onClose={() => setNegativePromptFor(null)}
          projectId={negativePromptFor.id}
          projectName={negativePromptFor.name}
        />
      ) : null}
    </Card>
  );
}
