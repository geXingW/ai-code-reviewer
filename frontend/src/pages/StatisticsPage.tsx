/**
 * 首页仪表盘：重新设计的网格布局，信息层次更清晰。
 * 顶部 - KPI 概览；中上 - 大图表 + 分类分布；中下 - 规则榜 + 项目活跃度。
 */

import * as React from 'react';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';

import {
  fetchStatsCategories,
  fetchStatsOverview,
  fetchStatsProjects,
  fetchStatsRules,
  fetchStatsTimeseries,
  type CategoryStat,
  type ProjectStat,
  type RuleStat,
  type StatsOverview,
  type TimeseriesPoint,
} from '../api';
import { relativeTime } from '../App';
import { categoryDisplay, severityDisplay } from '../lib/findingTaxonomy';

/** 支持的时间窗口切换选项。 */
const RANGE_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 7, label: '最近 7 天' },
  { value: 30, label: '最近 30 天' },
  { value: 90, label: '最近 90 天' },
];

type StatsBundle = {
  overview: StatsOverview | null;
  rules: RuleStat[];
  projects: ProjectStat[];
  categories: CategoryStat[];
  timeseries: TimeseriesPoint[];
};

const EMPTY_BUNDLE: StatsBundle = {
  overview: null,
  rules: [],
  projects: [],
  categories: [],
  timeseries: [],
};

/** 主页面：仪表盘网格布局。 */
export function StatisticsPage(): React.ReactElement {
  const [days, setDays] = React.useState<number>(30);
  const [bundle, setBundle] = React.useState<StatsBundle>(EMPTY_BUNDLE);
  const [loading, setLoading] = React.useState<boolean>(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [overview, rules, projects, categories, timeseries] = await Promise.all([
          fetchStatsOverview(days),
          fetchStatsRules(days, 10),
          fetchStatsProjects(days, 6),
          fetchStatsCategories(days),
          fetchStatsTimeseries(days),
        ]);
        if (active) {
          setBundle({ overview, rules, projects, categories, timeseries });
        }
      } catch (caught) {
        if (active) {
          setError(caught instanceof Error ? caught.message : '统计数据加载失败');
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [days]);

  const { overview, rules, projects, categories, timeseries } = bundle;
  const hasAnyData =
    (overview?.total_reviews ?? 0) > 0 ||
    rules.length > 0 ||
    projects.length > 0 ||
    categories.length > 0 ||
    timeseries.some((p) => p.review_count > 0 || p.finding_count > 0);

  return (
    <div className="space-y-5">
      {/* 顶部：标题 + 时间选择器 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-zinc-900">仪表盘</h1>
          <p className="mt-1 text-[13px] text-zinc-500">
            代码审查全局概览，实时掌握项目健康度。
          </p>
        </div>
        <RangeSelector value={days} onChange={setDays} />
      </div>

      {error ? (
        <div
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-700"
        >
          {error}
        </div>
      ) : null}

      {/* 第一行：KPI 卡片横排 */}
      {loading && !overview ? <KpiSkeletonRow /> : <KpiRow overview={overview} />}

      {!loading && !error && overview && !hasAnyData ? (
        <div
          data-testid="stats-empty"
          className="rounded-md border border-dashed border-zinc-300 bg-white px-6 py-10 text-center text-[13px] text-zinc-500"
        >
          暂无审查数据，先跑一次审查再来看看。
        </div>
      ) : null}

      {/* 第二行：大图表（左） + 分类分布（右） */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <TimeseriesSection points={timeseries} />
        <CategoriesSection categories={categories} />
      </div>

      {/* 第三行：规则榜（左） + 项目活跃度（右） */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <RulesSection rules={rules} />
        <ProjectsSection projects={projects} />
      </div>
    </div>
  );
}

// ---------------- 时间窗口切换 ----------------

function RangeSelector(props: {
  value: number;
  onChange: (v: number) => void;
}): React.ReactElement {
  return (
    <div
      role="group"
      aria-label="统计时间窗口"
      className="inline-flex rounded-md border border-zinc-200 bg-white p-0.5 text-[12px]"
    >
      {RANGE_OPTIONS.map((opt) => {
        const active = opt.value === props.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => props.onChange(opt.value)}
            className={cn(
              'px-3 py-1.5 rounded-[4px] transition-colors',
              active
                ? 'bg-zinc-900 text-white'
                : 'text-zinc-600 hover:bg-zinc-100',
            )}
            aria-pressed={active}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------- KPI 卡片（重新设计：渐变背景 + 大数字） ----------------

function KpiSkeletonRow(): React.ReactElement {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          data-testid="kpi-skeleton"
          className="h-28 animate-pulse rounded-xl border border-zinc-200 bg-white"
        />
      ))}
    </div>
  );
}

function KpiRow(props: { overview: StatsOverview | null }): React.ReactElement {
  const o = props.overview;
  const totalReviews = o?.total_reviews ?? 0;
  const totalFindings = o?.total_findings ?? 0;
  const blockers = o?.total_blockers ?? 0;
  const activeProjects = o?.active_projects ?? 0;
  const avgSeconds = o?.avg_duration_ms != null ? (o.avg_duration_ms / 1000).toFixed(2) : '—';

  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      <KpiCard
        label="总审查"
        value={String(totalReviews)}
        hint="次"
        gradient="from-indigo-50 to-indigo-100"
        accent="text-indigo-600"
        border="border-indigo-200"
      />
      <KpiCard
        label="总问题"
        value={String(totalFindings)}
        hint={`BLOCKER ${blockers}`}
        gradient="from-rose-50 to-rose-100"
        accent="text-rose-600"
        border="border-rose-200"
      />
      <KpiCard
        label="平均耗时"
        value={`${avgSeconds}s`}
        hint="平均每次审查"
        gradient="from-emerald-50 to-emerald-100"
        accent="text-emerald-600"
        border="border-emerald-200"
      />
      <KpiCard
        label="活跃项目"
        value={String(activeProjects)}
        hint="个"
        gradient="from-amber-50 to-amber-100"
        accent="text-amber-600"
        border="border-amber-200"
      />
    </div>
  );
}

function KpiCard(props: {
  label: string;
  value: string;
  hint?: string;
  gradient: string;
  accent: string;
  border: string;
}): React.ReactElement {
  return (
    <div
      data-testid="kpi-card"
      className={cn(
        'rounded-xl border bg-gradient-to-br p-5 transition-transform hover:scale-[1.02]',
        props.gradient,
        props.border,
      )}
    >
      <div className="text-[12px] font-medium uppercase tracking-wide text-zinc-500">
        {props.label}
      </div>
      <div className={cn('mt-3 text-3xl font-bold', props.accent)}>{props.value}</div>
      {props.hint ? (
        <div className="mt-1 text-[11px] text-zinc-500">{props.hint}</div>
      ) : null}
    </div>
  );
}

// ---------------- 时间趋势（更宽的图表，视觉重点） ----------------

function TimeseriesSection(props: { points: TimeseriesPoint[] }): React.ReactElement {
  const { points } = props;
  const max = Math.max(1, ...points.map((p) => p.review_count));

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">审查趋势</CardTitle>
        <CardDescription>按天统计审查量、问题量、BLOCKER 量。</CardDescription>
      </CardHeader>
      <CardContent className="pb-4">
        {points.length === 0 ? (
          <div className="py-10 text-center text-[13px] text-zinc-500">暂无时间序列数据</div>
        ) : (
          <div className="flex items-end gap-[3px] overflow-x-auto pb-2" role="list" aria-label="时间趋势柱状">
            {points.map((p) => {
              const heightPct = Math.round((p.review_count / max) * 100);
              const hasFindings = p.finding_count > 0;
              const hasBlockers = p.blocker_count > 0;
              const label = `${p.date}：审查 ${p.review_count}、问题 ${p.finding_count}、BLOCKER ${p.blocker_count}`;
              return (
                <div
                  key={p.date}
                  role="listitem"
                  data-testid="timeseries-bar"
                  data-date={p.date}
                  data-review-count={p.review_count}
                  className="flex min-w-[12px] flex-col items-center gap-1"
                  title={label}
                >
                  <div className="relative flex h-32 w-3 items-end">
                    {/* 堆叠柱状：下层审查数，上层问题数 */}
                    <div
                      className={cn(
                        'absolute bottom-0 w-full rounded-t-sm',
                        p.review_count === 0 ? 'bg-zinc-100' : 'bg-indigo-400',
                      )}
                      style={{ height: `${Math.max(heightPct, p.review_count === 0 ? 4 : 6)}%` }}
                    />
                    {/* BLOCKER 红点提示 */}
                    {hasBlockers ? (
                      <div className="absolute -top-1 left-1/2 h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-rose-500" />
                    ) : null}
                    {/* 问题数顶部标记 */}
                    {hasFindings && p.finding_count > p.review_count ? (
                      <div className="absolute -top-0.5 left-1/2 h-0.5 w-0.5 -translate-x-1/2 rounded-full bg-amber-400" />
                    ) : null}
                  </div>
                  <span className="text-[10px] text-zinc-400">
                    {p.date.slice(5)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------- 问题分类分布（小卡片版，适合右侧栏） ----------------

function CategoriesSection(props: { categories: CategoryStat[] }): React.ReactElement {
  const { categories } = props;
  const total = categories.reduce((sum, c) => sum + c.count, 0);

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">问题分布</CardTitle>
        <CardDescription>按规则分类统计问题占比。</CardDescription>
      </CardHeader>
      <CardContent className="pb-4">
        {categories.length === 0 ? (
          <div className="py-8 text-center text-[13px] text-zinc-500" data-testid="categories-empty">
            暂无分类数据
          </div>
        ) : (
          <div className="space-y-2" role="list" aria-label="问题分类分布">
            {categories.map((cat) => {
              const cd = categoryDisplay(cat.category);
              const pct = total > 0 ? Math.round((cat.count / total) * 100) : 0;
              return (
                <div
                  key={cat.category}
                  role="listitem"
                  data-testid="category-row"
                  data-category={cat.category}
                  className="space-y-1"
                >
                  <div className="flex items-center justify-between text-[12px]">
                    <span className="flex items-center gap-1.5 text-zinc-700">
                      <span>{cd.emoji}</span>
                      <span className="truncate">{cd.label}</span>
                    </span>
                    <span className="font-semibold text-zinc-900">{cat.count}</span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-100">
                    <div
                      className="h-full rounded-full bg-indigo-500 transition-all"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------- 规则命中榜 Top 10 ----------------

function fpRateBucket(rate: number): { label: string; className: string } {
  const pct = rate * 100;
  if (pct >= 30) {
    return { label: `${pct.toFixed(1)}%`, className: 'bg-red-100 text-red-700' };
  }
  if (pct >= 10) {
    return { label: `${pct.toFixed(1)}%`, className: 'bg-yellow-100 text-yellow-800' };
  }
  return { label: `${pct.toFixed(1)}%`, className: 'bg-zinc-100 text-zinc-600' };
}

function RulesSection(props: { rules: RuleStat[] }): React.ReactElement {
  const { rules } = props;
  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">高频规则 Top 10</CardTitle>
        <CardDescription>命中数最高的规则，标注误报率。</CardDescription>
      </CardHeader>
      <CardContent className="pb-4">
        {rules.length === 0 ? (
          <div className="py-6 text-center text-[13px] text-zinc-500" data-testid="rules-empty">
            暂无问题
          </div>
        ) : (
          <div className="divide-y divide-zinc-100" role="list" aria-label="规则命中榜">
            <div className="grid grid-cols-[minmax(0,2fr)_40px_50px] gap-2 py-1.5 text-[10px] font-medium uppercase text-zinc-400">
              <div>规则</div>
              <div className="text-right">命中</div>
              <div className="text-right">误报</div>
            </div>
            {rules.slice(0, 8).map((rule) => {
              const sev = severityDisplay(rule.severity_default);
              const cat = categoryDisplay(rule.category_default);
              const fp = fpRateBucket(rule.fp_rate);
              return (
                <div
                  key={rule.rule_id}
                  role="listitem"
                  data-testid="rule-row"
                  data-rule-id={rule.rule_id}
                  className="grid grid-cols-[minmax(0,2fr)_40px_50px] items-center gap-2 py-1.5 text-[12px]"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span title={sev.label}>{sev.emoji}</span>
                      <span className="truncate font-mono text-[11px] text-zinc-600">
                        {rule.rule_id}
                      </span>
                    </div>
                    <div className="truncate text-[11px] text-zinc-400">
                      {rule.title ?? '（规则已删除）'}
                    </div>
                  </div>
                  <div className="text-right font-semibold text-zinc-900">
                    {rule.finding_count}
                  </div>
                  <div className="text-right">
                    <span className={cn('rounded px-1 py-0.5 text-[10px]', fp.className)}>
                      {fp.label}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------- 项目活跃度 Top 6 ----------------

function ProjectsSection(props: { projects: ProjectStat[] }): React.ReactElement {
  const { projects } = props;
  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">活跃项目 Top 6</CardTitle>
        <CardDescription>按审查数排序，展示问题量与 BLOCKER 数。</CardDescription>
      </CardHeader>
      <CardContent className="pb-4">
        {projects.length === 0 ? (
          <div className="py-6 text-center text-[13px] text-zinc-500" data-testid="projects-empty">
            暂无项目
          </div>
        ) : (
          <div className="divide-y divide-zinc-100" role="list" aria-label="项目活跃度">
            <div className="grid grid-cols-[minmax(0,1.5fr)_35px_35px] gap-2 py-1.5 text-[10px] font-medium uppercase text-zinc-400">
              <div>项目</div>
              <div className="text-right">审查</div>
              <div className="text-right">问题</div>
            </div>
            {projects.slice(0, 6).map((p) => (
              <div
                key={p.project_id}
                role="listitem"
                data-testid="project-row"
                data-project-id={p.project_id}
                className="grid grid-cols-[minmax(0,1.5fr)_35px_35px] items-center gap-2 py-1.5 text-[12px]"
              >
                <div className="truncate text-zinc-700">{p.project_name}</div>
                <div className="text-right font-semibold text-zinc-900">{p.review_count}</div>
                <div className="text-right">
                  <span className={cn(
                    p.blocker_count > 0 ? 'text-rose-600 font-medium' : 'text-zinc-500'
                  )}>
                    {p.finding_count}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
