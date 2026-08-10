import { useMemo, useState } from 'react';
import { Search } from 'lucide-react';

import { RuleConfig } from '../api';
import { Button } from './ui/button';
import {
  CATEGORY_ORDER,
  FindingCategory,
  SEVERITY_ORDER,
  Severity,
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
  const base =
    'inline-flex items-center gap-1 px-2 py-[3px] rounded-full border text-[12px] leading-none transition-colors';
  const activeCls = active
    ? 'bg-zinc-900 text-white border-zinc-900 shadow-sm'
    : 'bg-white text-zinc-600 border-zinc-200 hover:border-zinc-300 hover:bg-zinc-50';
  const disabledCls = disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer';
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={onClick}
      className={`${base} ${activeCls} ${disabledCls}`}
    >
      {children}
    </button>
  );
}

/**
 * 规则勾选面板：搜索 / 严重度 / 分类 / 标签 多维筛选 + 批量操作。
 *
 * 筛选合成：同维度多选 = OR，跨维度 = AND。已选的规则即便被筛除也保留在
 * selectedRuleIds 中——批量按钮"取消全选"是唯一显式清空入口。
 */
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

  // Chip 命中数：每个 chip 显示"当前筛选条件下若加上/只保留这个 chip 时"的规则数——
  // 为了不让 chip 数字随其他维度剧烈跳动，chip count 只显示该维度独立命中的规则数（
  // 忽略同维度其他 chip 的选中，但保留其他维度的过滤）。
  const severityCounts = useMemo(() => {
    const counts = new Map<Severity, number>();
    for (const s of SEVERITY_ORDER) counts.set(s, 0);
    for (const rule of rules) {
      if (!isKnownSeverity(rule.severity_default)) continue;
      counts.set(rule.severity_default.toUpperCase() as Severity, (counts.get(rule.severity_default.toUpperCase() as Severity) ?? 0) + 1);
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

  // 标签命中数：与其它维度一致，只统计该维度独立命中的规则数。
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
        <label className="text-[12px] font-medium text-zinc-500 mb-2 block">启用规则</label>
        <div className="rounded-md border border-dashed border-zinc-200 bg-zinc-50/40">
          <div className="p-4 text-[12px] text-zinc-400 text-center">暂无规则，请先到"审查规则"页面创建。</div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <label className="text-[12px] font-medium text-zinc-500 mb-2 block">启用规则</label>

      {/* 搜索框 */}
      <div className="relative mb-2.5">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-zinc-400" aria-hidden />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="输入 rule_id 或标题关键字"
          aria-label="搜索规则"
          className="w-full rounded-md border border-[#E4E4E7] bg-white py-1.5 pl-9 pr-3 text-[13px] placeholder:text-zinc-400 hover:border-[#D4D4D8] focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      {/* 严重度 chip */}
      <div className="mb-2 flex items-center flex-wrap gap-1.5">
        <span className="text-[11px] font-medium text-zinc-400 mr-0.5">严重度</span>
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
      <div className="mb-2 flex items-center flex-wrap gap-1.5">
        <span className="text-[11px] font-medium text-zinc-400 mr-0.5">分类</span>
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
        <div className="mb-2.5 flex items-center flex-wrap gap-1.5">
          <span className="text-[11px] font-medium text-zinc-400 mr-0.5">标签</span>
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
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2 rounded-md bg-zinc-50/70 px-2.5 py-1.5">
        <div className="text-[11px] text-zinc-500 flex items-center gap-2">
          <span>
            已选 <span className="text-zinc-800 font-semibold">{selectedRuleIds.length}</span> / 可见{' '}
            <span className="text-zinc-800 font-semibold">{visibleRules.length}</span> / 总{' '}
            <span className="text-zinc-800 font-semibold">{rules.length}</span>
          </span>
          {hasAnyFilter ? (
            <button
              type="button"
              onClick={clearAllFilters}
              className="text-indigo-600 hover:text-indigo-800 underline underline-offset-2 text-[11px]"
            >
              清除所有筛选
            </button>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={selectAllVisible}
            disabled={visibleRules.length === 0}
          >
            全选可见
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={clearAllSelected}
            disabled={selectedRuleIds.length === 0}
          >
            取消全选
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={selectVisibleBlockers}
            disabled={visibleRules.length === 0}
          >
            勾选可见 BLOCKER
          </Button>
        </div>
      </div>

      {/* 列表 */}
      <div className="rounded-md border border-zinc-200 max-h-64 overflow-y-auto divide-y divide-zinc-100">
        {visibleRules.length === 0 ? (
          <div className="p-3 text-[12px] text-zinc-500 flex items-center gap-2">
            <span>当前筛选条件无匹配规则。</span>
            <button
              type="button"
              onClick={clearAllFilters}
              className="text-indigo-600 hover:text-indigo-800 underline underline-offset-2 text-[11px]"
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
                className={`flex items-center gap-2 px-3 py-2 text-[13px] cursor-pointer transition-colors hover:bg-zinc-50 ${checked ? 'bg-indigo-50/40' : ''}`}
                title={rule.prompt_snippet}
              >
                <input
                  type="checkbox"
                  className="size-4 rounded border-zinc-300 accent-indigo-600"
                  checked={checked}
                  onChange={(event) => onToggle(rule.id, event.target.checked)}
                  aria-label={`选中规则 ${rule.rule_id}`}
                />
                <span aria-hidden>{sevDisp.emoji}</span>
                <span className="text-zinc-400 text-[11px] font-medium">[{sevDisp.label}]</span>
                <span aria-hidden>{catDisp.emoji}</span>
                <span className="text-zinc-900 font-mono text-[12px]">{rule.rule_id}</span>
                <span className="text-zinc-300">·</span>
                <span className="text-zinc-600 truncate">{rule.title}</span>
              </label>
            );
          })
        )}
      </div>
    </div>
  );
}
