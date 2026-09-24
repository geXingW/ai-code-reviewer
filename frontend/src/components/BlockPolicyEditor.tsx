/**
 * 项目阻断策略编辑器：分支匹配 × 阻断级别 × 引擎错误阻断，拖拽调整优先级。
 *
 * 从 App.tsx 内联组件抽出并 antd 化（原 .policy-* 全局 CSS 类一并移除，
 * 样式改走 Tailwind utility）。拖拽逻辑与原实现一致：HTML5 drag events，
 * 保存时按当前顺序生成 priority = index + 1。
 */

import { useEffect, useState, type DragEvent } from 'react';
import { HolderOutlined, PlusOutlined } from '@ant-design/icons';
import { App as AntApp, Button, Checkbox, Input, Select } from 'antd';

import type { BlockPolicy, BlockPolicyPayload, BlockPolicySeverity } from '../api';

const BLOCK_POLICY_SEVERITY_OPTIONS: Array<{ value: BlockPolicySeverity; label: string }> = [
  { value: 'NONE', label: 'NONE（不阻断）' },
  { value: 'INFO', label: 'INFO' },
  { value: 'WARNING', label: 'WARNING' },
  { value: 'BLOCKER', label: 'BLOCKER' },
];

type EditablePolicy = Omit<BlockPolicyPayload, 'priority'> & { key: string };

let keySeed = 0;

function toEditable(policy: BlockPolicy): EditablePolicy {
  return {
    key: policy.id,
    branch_pattern: policy.branch_pattern,
    block_severity: policy.block_severity,
    block_on_engine_error: policy.block_on_engine_error,
    require_all_resolved: policy.require_all_resolved,
  };
}

interface BlockPolicyEditorProps {
  projectId: string;
  policies: BlockPolicy[];
  onSave: (projectId: string, policies: BlockPolicyPayload[]) => Promise<void>;
}

export function BlockPolicyEditor({ projectId, policies, onSave }: BlockPolicyEditorProps) {
  const { message } = AntApp.useApp();
  const [items, setItems] = useState<EditablePolicy[]>(() => policies.map(toEditable));
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setItems(policies.map(toEditable));
  }, [policies]);

  function updateItem(index: number, patch: Partial<EditablePolicy>) {
    setItems((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  }

  function addPolicy() {
    keySeed += 1;
    setItems((prev) => [
      ...prev,
      {
        key: `new-policy-${keySeed}`,
        branch_pattern: '',
        block_severity: 'WARNING',
        block_on_engine_error: false,
        require_all_resolved: false,
      },
    ]);
  }

  function removePolicy(index: number) {
    setItems((prev) => prev.filter((_, i) => i !== index));
  }

  function handleDragStart(index: number) {
    setDragIndex(index);
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
  }

  function handleDrop(index: number) {
    if (dragIndex === null || dragIndex === index) {
      setDragIndex(null);
      return;
    }
    setItems((prev) => {
      const next = [...prev];
      const [moved] = next.splice(dragIndex, 1);
      next.splice(index, 0, moved);
      return next;
    });
    setDragIndex(null);
  }

  async function handleSave() {
    if (saving) {
      return;
    }
    if (items.some((item) => !item.branch_pattern.trim())) {
      message.error('每条策略的分支匹配不能为空。');
      return;
    }
    const payload: BlockPolicyPayload[] = items.map((item, index) => ({
      branch_pattern: item.branch_pattern,
      block_severity: item.block_severity,
      block_on_engine_error: item.block_on_engine_error,
      require_all_resolved: item.require_all_resolved,
      priority: index + 1,
    }));
    try {
      setSaving(true);
      await onSave(projectId, payload);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-3 overflow-x-auto">
      <div className="grid grid-cols-[24px_36px_minmax(140px,1fr)_170px_120px_auto] items-center gap-2.5 border-b border-zinc-100 pb-2 text-[12px] font-medium text-zinc-500">
        <span />
        <span className="text-center">序号</span>
        <span>分支匹配</span>
        <span>阻断级别</span>
        <span>引擎错误阻断</span>
        <span>操作</span>
      </div>

      {items.length === 0 ? (
        <div className="py-6 text-center text-[13px] text-zinc-400">
          暂无阻断策略，点击下方按钮添加
        </div>
      ) : null}

      {items.map((item, index) => (
        <div
          key={item.key}
          className={`grid grid-cols-[24px_36px_minmax(140px,1fr)_170px_120px_auto] items-center gap-2.5 border-b border-zinc-100 py-2 ${
            dragIndex === index ? 'opacity-40' : ''
          }`}
          onDragOver={handleDragOver}
          onDrop={() => handleDrop(index)}
        >
          <span
            className="cursor-grab select-none text-center text-[14px] leading-none text-slate-400 active:cursor-grabbing"
            draggable
            aria-label="拖动排序"
            onDragStart={() => handleDragStart(index)}
            onDragEnd={() => setDragIndex(null)}
          >
            <HolderOutlined />
          </span>
          <span className="text-center text-[12px] font-semibold text-zinc-500">{index + 1}</span>
          <Input
            value={item.branch_pattern}
            placeholder="如 master 或 release/*"
            onChange={(event) => updateItem(index, { branch_pattern: event.target.value })}
          />
          <Select
            value={item.block_severity}
            options={BLOCK_POLICY_SEVERITY_OPTIONS}
            onChange={(value) => updateItem(index, { block_severity: value })}
          />
          <Checkbox
            checked={item.block_on_engine_error}
            onChange={(event) => updateItem(index, { block_on_engine_error: event.target.checked })}
          >
            阻断
          </Checkbox>
          <Button danger size="small" onClick={() => removePolicy(index)}>
            删除
          </Button>
        </div>
      ))}

      <div className="mt-3 flex gap-3">
        <Button icon={<PlusOutlined />} onClick={addPolicy}>
          添加策略
        </Button>
        <Button type="primary" loading={saving} onClick={() => void handleSave()}>
          保存策略
        </Button>
      </div>
    </div>
  );
}
