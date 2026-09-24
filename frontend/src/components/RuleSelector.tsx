/**
 * 规则勾选面板（antd 版）：搜索 / 严重度 / 分类 / 标签 多维筛选 + 批量操作。
 *
 * 规则数据由调用方全量拉取（fetchRules 循环翻页），故本面板的本地筛选
 * 是完整的（与列表页的服务端筛选场景不同）。
 * 筛选合成：同维度多选 = OR，跨维度 = AND。已选的规则即便被筛除也保留在
 * selectedRuleIds 中——批量按钮「取消全选」是唯一显式清空入口。
 */

import { useMemo, useState } from 'react';
import { SearchOutlined } from '@ant-design/icons';
import { Button, Checkbox, Input } from 'antd';

import type { RuleConfig } from '../api';
import {
  CATEGORY_ORDER,
  type FindingCategory,
  SEVERITY_ORDER,
  type Severity,
  categoryDisplay,
  isKnownCategory,
  isKnownSeverity,
  severityDisplay,
} from '../lib/findingTaxonomy';

export interface RuleSelectorProps {
  /** 全部可用的规则（不做分页；调用方保证已加载全量） */
  rules: RuleConfig[];
  /** 已选中的规则 UUID 列表 */
  selectedRuleIds: string[];
  /** 用户切换单条规则选中状态时的回调 */
  onToggle: (ruleId: string, enabled: boolean) => void;
  /** 批量替换选中集合（用于全选可见 / 取消全选 / 勾选可见 BLOCKER） */
  onBulkReplace: (ruleIds: string[]) => void;
}

/** 从一条规则里抽出去重、去空白后的标签数组（保持首次出现顺序）。 */
function ruleTags(rule: RuleConfig): string[] {
  const tags: string[] = [];
  const seen = new Set<string>();
  for (const raw of rule.tags ?? []) {
    const tag = typeof raw === 'string' ? raw.trim() : '';
    if (tag && !seen.has(tag)) {
      seen.add(tag);
      tags.push(tag);
    }
  }
  return tags;
}

/** severity 排序索引，未知走末位。 */
function severityRank(value: string | null | undefined): number {
  if (!value) return SEVERITY_ORDER.length;
  const idx = (SEVERITY_ORDER as readonly string[]).indexOf(value.toUpperCase());
  return idx === -1 ? SEVERITY_ORDER.length : idx;
}

interface ChipProps {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
  ariaLabel?: string;
}

function Chip({ active, disabled, onClick, children, ariaLabel }: ChipProps) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-[3px] text-[12px] leading-none transition-colors ${
        active
          ? 'border-zinc-900 bg-zinc-900 text-white shadow-sm'
          : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-300 hover:bg-zinc-50'
      } ${disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'}`}
    >
      {children}
    </button>
  );
}

export function RuleSelector({ rules, selectedRuleIds, onToggle, onBulkReplace }: RuleSelectorProps) {
  const [search, setSearch] = useState('');
  const [severityFilter, setSeverityFilter] = useState<Set<Severity>>(new Set());
  const [categoryFilter, setCategoryFilter] = useState<Set<FindingCategory>>(new Set());
  const [tagFilter, setTagFilter] = useState<Set<string>>(new Set());

  const selectedSet = useMemo(() => new Set(selectedRuleIds), [selectedRuleIds]);

  // 聚合所有出现过的标签（去重），用于渲染标签 chip；按字母序排序。
  const allTags = useMemo(() => {
    const tags = new Set<string>();
    for (const rule of rules) {
      for (const tag of ruleTags(rule)) {
        tags.add(tag);
      }
    }
    return [...tags].sort((a, b) => a.localeCompare(b));
  }, [rules]);

  // 筛选后的可见规则；应用搜索 / severity / category / tags 四层。
  const visibleRules = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = rules.filter((rule) => {
      if (q) {
        const hit =
          rule.rule_id.toLowerCase().includes(q) || rule.title.toLowerCase().includes(q);
        if (!hit) return false;
      }
      if (severityFilter.size > 0) {
        if (!isKnownSeverity(rule.severity_default)) return false;
        const upper = rule.severity_default.toUpperCase() as Severity;
        if (!severityFilter.has(upper)) return false;
      }
      if (categoryFilter.size > 0) {
        const cat = isKnownCategory(rule.category_default) ? (rule.category_default as FindingCategory) : 'other';
        if (!categoryFilter.has(cat)) return false;
      }
      // 标签筛选：多标签为 OR，规则命中任一选中标签即保留；无标签规则不命中。
      if (tagFilter.size > 0) {
        const ruleTagSet = new Set(ruleTags(rule));
        let hit = false;
        for (const tag of tagFilter) {
          if (ruleTagSet.has(tag)) {
            hit = true;
            break;
          }
        }
        if (!hit) return false;
      }
      return true;
    });
    // 排序：severity 优先（BLOCKER→WARNING→INFO→未知），同级按 rule_id 字母序。
    return filtered.slice().sort((a, b) => {
      const rankDiff = severityRank(a.severity_default) - severityRank(b.severity_default);
      if (rankDiff !== 0) return rankDiff;
      return a.rule_id.localeCompare(b.rule_id);
    });
  }, [rules, search, severityFilter, categoryFilter, tagFilter]);

  const severityCounts = useMemo(() => {
    const counts = new Map<Severity, number>();
    for (const s of SEVERITY_ORDER) counts.set(s, 0);
    for (const rule of rules) {
      if (!isKnownSeverity(rule.severity_default)) continue;
      counts.set(
        rule.severity_default.toUpperCase() as Severity,
        (counts.get(rule.severity_default.toUpperCase() as Severity) ?? 0) + 1,
      );
    }
    return counts;
  }, [rules]);

  const categoryCounts = useMemo(() => {
    const counts = new Map<FindingCategory, number>();
    for (const c of CATEGORY_ORDER) counts.set(c, 0);
    for (const rule of rules) {
      const cat = isKnownCategory(rule.category_default) ? (rule.category_default as FindingCategory) : 'other';
      counts.set(cat, (counts.get(cat) ?? 0) + 1);
    }
    return counts;
  }, [rules]);

  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const tag of allTags) counts.set(tag, 0);
    for (const rule of rules) {
      const ruleTagSet = new Set(ruleTags(rule));
      for (const tag of ruleTagSet) {
        counts.set(tag, (counts.get(tag) ?? 0) + 1);
      }
    }
    return counts;
  }, [rules, allTags]);

  const hasAnyFilter =
    search.trim().length > 0 ||
    severityFilter.size > 0 ||
    categoryFilter.size > 0 ||
    tagFilter.size > 0;

  function toggleSeverity(sev: Severity) {
    setSeverityFilter((prev) => {
      const next = new Set(prev);
      if (next.has(sev)) next.delete(sev);
      else next.add(sev);
      return next;
    });
  }

  function toggleCategory(cat: FindingCategory) {
    setCategoryFilter((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  }

  function toggleTag(tag: string) {
    setTagFilter((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }

  function clearAllFilters() {
    setSearch('');
    setSeverityFilter(new Set());
    setCategoryFilter(new Set());
    setTagFilter(new Set());
  }

  function selectAllVisible() {
    const merged = new Set(selectedRuleIds);
    for (const rule of visibleRules) merged.add(rule.id);
    onBulkReplace([...merged]);
  }

  function clearAllSelected() {
    onBulkReplace([]);
  }

  function selectVisibleBlockers() {
    const merged = new Set(selectedRuleIds);
    for (const rule of visibleRules) {
      if (isKnownSeverity(rule.severity_default) && rule.severity_default.toUpperCase() === 'BLOCKER') {
        merged.add(rule.id);
      }
    }
    onBulkReplace([...merged]);
  }

  if (rules.length === 0) {
    return (
      <div>
        <label className="mb-2 block text-[12px] font-medium text-zinc-500">启用规则</label>
        <div className="rounded-md border border-dashed border-zinc-200 bg-zinc-50/40">
          <div className="p-4 text-center text-[12px] text-zinc-400">
            暂无规则，请先到「审查规则」页面创建。
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <label className="mb-2 block text-[12px] font-medium text-zinc-500">启用规则</label>

      {/* 搜索框 */}
      <Input
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder="输入 rule_id 或标题关键字"
        aria-label="搜索规则"
        allowClear
        className="mb-2.5"
        prefix={<SearchOutlined style={{ color: '#A1A1AA' }} />}
      />

      {/* 严重度 chip */}
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <span className="mr-0.5 text-[11px] font-medium text-zinc-400">严重度</span>
        {SEVERITY_ORDER.map((sev) => {
          const disp = severityDisplay(sev);
          const count = severityCounts.get(sev) ?? 0;
          return (
            <Chip
              key={sev}
              active={severityFilter.has(sev)}
              disabled={count === 0}
              onClick={() => toggleSeverity(sev)}
              ariaLabel={`筛选严重度 ${disp.label}`}
            >
              <span>{disp.emoji}</span>
              <span>{disp.label}</span>
              <span className="text-[10px] opacity-70">{count}</span>
            </Chip>
          );
        })}
      </div>

      {/* 分类 chip */}
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <span className="mr-0.5 text-[11px] font-medium text-zinc-400">分类</span>
        {CATEGORY_ORDER.map((cat) => {
          const disp = categoryDisplay(cat);
          const count = categoryCounts.get(cat) ?? 0;
          return (
            <Chip
              key={cat}
              active={categoryFilter.has(cat)}
              disabled={count === 0}
              onClick={() => toggleCategory(cat)}
              ariaLabel={`筛选分类 ${disp.label}`}
            >
              <span>{disp.emoji}</span>
              <span>{disp.label}</span>
              <span className="text-[10px] opacity-70">{count}</span>
            </Chip>
          );
        })}
      </div>

      {/* 标签 chip */}
      {allTags.length > 0 ? (
        <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-[11px] font-medium text-zinc-400">标签</span>
          {allTags.map((tag) => {
            const count = tagCounts.get(tag) ?? 0;
            return (
              <Chip
                key={tag}
                active={tagFilter.has(tag)}
                disabled={count === 0}
                onClick={() => toggleTag(tag)}
                ariaLabel={`筛选标签 ${tag}`}
              >
                <span>#{tag}</span>
                <span className="text-[10px] opacity-70">{count}</span>
              </Chip>
            );
          })}
        </div>
      ) : null}

      {/* 计数栏 + 批量按钮 */}
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-md bg-zinc-50/70 px-2.5 py-1.5">
        <div className="flex items-center gap-2 text-[11px] text-zinc-500">
          <span>
            已选 <span className="font-semibold text-zinc-800">{selectedRuleIds.length}</span> / 可见{' '}
            <span className="font-semibold text-zinc-800">{visibleRules.length}</span> / 总{' '}
            <span className="font-semibold text-zinc-800">{rules.length}</span>
          </span>
          {hasAnyFilter ? (
            <button
              type="button"
              onClick={clearAllFilters}
              className="text-[11px] text-indigo-600 underline underline-offset-2 hover:text-indigo-800"
            >
              清除所有筛选
            </button>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5">
          <Button size="small" onClick={selectAllVisible} disabled={visibleRules.length === 0}>
            全选可见
          </Button>
          <Button size="small" onClick={clearAllSelected} disabled={selectedRuleIds.length === 0}>
            取消全选
          </Button>
          <Button size="small" onClick={selectVisibleBlockers} disabled={visibleRules.length === 0}>
            勾选可见 BLOCKER
          </Button>
        </div>
      </div>

      {/* 列表 */}
      <div className="max-h-64 divide-y divide-zinc-100 overflow-y-auto rounded-md border border-zinc-200">
        {visibleRules.length === 0 ? (
          <div className="flex items-center gap-2 p-3 text-[12px] text-zinc-500">
            <span>当前筛选条件无匹配规则。</span>
            <button
              type="button"
              onClick={clearAllFilters}
              className="text-[11px] text-indigo-600 underline underline-offset-2 hover:text-indigo-800"
            >
              清除所有筛选
            </button>
          </div>
        ) : (
          visibleRules.map((rule) => {
            const sevDisp = severityDisplay(rule.severity_default);
            const catDisp = categoryDisplay(rule.category_default);
            const checked = selectedSet.has(rule.id);
            return (
              <label
                key={rule.id}
                className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-[13px] transition-colors hover:bg-zinc-50 ${
                  checked ? 'bg-indigo-50/40' : ''
                }`}
                title={rule.prompt_snippet}
              >
                <Checkbox
                  checked={checked}
                  onChange={(event) => onToggle(rule.id, event.target.checked)}
                  aria-label={`选中规则 ${rule.rule_id}`}
                />
                <span aria-hidden>{sevDisp.emoji}</span>
                <span className="text-[11px] font-medium text-zinc-400">[{sevDisp.label}]</span>
                <span aria-hidden>{catDisp.emoji}</span>
                <span className="font-mono text-[12px] text-zinc-900">{rule.rule_id}</span>
                <span className="text-zinc-300">·</span>
                <span className="truncate text-zinc-600">{rule.title}</span>
              </label>
            );
          })
        )}
      </div>
    </div>
  );
}
