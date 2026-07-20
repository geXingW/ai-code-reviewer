/**
 * 统计页（PR-A）：规则效果、项目活跃度、时间趋势、分类分布。
 * 图表纯 Tailwind + CSS bar（不引入 chart 库），日期用原生 Date（不引入 dayjs）。
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

/** 主页面：区块 = 时间切换 + 5 个 section（KPI / 趋势 / 规则 / 项目 / 分类）。 */
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
          fetchStatsProjects(days, 10),
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
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-zinc-900">统计</h1>
          <p className="mt-1 text-[13px] text-zinc-500">
            规则效果、项目活跃度、时间趋势 —— 为规则升降级决策提供数据依据。
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

      {loading && !overview ? (
        <KpiSkeletonRow />
      ) : (
        <KpiRow overview={overview} />
      )}

      {!loading && !error && overview && !hasAnyData ? (
        <div
          data-testid="stats-empty"
          className="rounded-md border border-dashed border-zinc-300 bg-white px-6 py-10 text-center text-[13px] text-zinc-500"
        >
          暂无审查数据，先跑一次审查再来看看。
        </div>
      ) : null}

      <TimeseriesSection points={timeseries} />

      <RulesSection rules={rules} />

      <ProjectsSection projects={projects} />

      <CategoriesSection categories={categories} />
    </div>
  );
}

// ---------------- 时间窗口切换 ----------------

function RangeSelector(props: {
  value: number;
  onChange: (v: number) => void;
}): React.ReactElement {
  return (
    <div role="group" aria-label="统计时间窗口" className="inline-flex rounded-md border border-zinc-200 bg-white p-0.5 text-[12px]">
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

// ---------------- KPI 卡片 ----------------

function KpiSkeletonRow(): React.ReactElement {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          data-testid="kpi-skeleton"
          className="h-24 animate-pulse rounded-lg border border-zinc-200 bg-white"
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
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
      <KpiCard
        label="总审查数"
        value={String(totalReviews)}
        hint="已排除 MR 生命周期事件"
      />
      <KpiCard
        label="总问题数"
        value={String(totalFindings)}
        hint={`BLOCKER ${blockers}`}
      />
      <KpiCard label="平均耗时" value={`${avgSeconds}s`} hint="排除未记录耗时" />
      <KpiCard label="活跃项目" value={String(activeProjects)} hint="窗口内至少 1 次真实审查" />
    </div>
  );
}

function KpiCard(props: { label: string; value: string; hint?: string }): React.ReactElement {
  return (
    <div
      data-testid="kpi-card"
      className="rounded-lg border border-zinc-200 bg-white p-4"
    >
      <div className="text-[12px] font-medium uppercase tracking-wide text-zinc-500">
        {props.label}
      </div>
      <div className="mt-2 text-2xl font-semibold text-zinc-900">{props.value}</div>
      {props.hint ? (
        <div className="mt-1 text-[12px] text-zinc-400">{props.hint}</div>
      ) : null}
    </div>
  );
}

// ---------------- 时间趋势（CSS bar） ----------------

function TimeseriesSection(props: { points: TimeseriesPoint[] }): React.ReactElement {
  const { points } = props;
  // 归一化高度：以窗口内最大值为基线；全 0 时避免除零。
  const max = Math.max(1, ...points.map((p) => p.review_count));

  return (
    <Card>
      <CardHeader>
        <CardTitle>时间趋势</CardTitle>
        <CardDescription>按天统计审查数、问题数、BLOCKER 数；缺失日期填 0。</CardDescription>
      </CardHeader>
      <CardContent>
        {points.length === 0 ? (
          <div className="py-6 text-center text-[13px] text-zinc-500">暂无时间序列数据</div>
        ) : (
          <div className="flex items-end gap-[2px] overflow-x-auto pb-2" role="list" aria-label="时间趋势柱状">
            {points.map((p) => {
              const heightPct = Math.round((p.review_count / max) * 100);
              const label = `${p.date}：审查 ${p.review_count}、问题 ${p.finding_count}、BLOCKER ${p.blocker_count}`;
              return (
                <div
                  key={p.date}
                  role="listitem"
                  data-testid="timeseries-bar"
                  data-date={p.date}
                  data-review-count={p.review_count}
                  className="flex min-w-[10px] flex-col items-center gap-1"
                  title={label}
                >
                  <div className="relative flex h-24 w-2 items-end">
                    <div
                      className={cn(
                        'w-full rounded-sm',
                        p.review_count === 0 ? 'bg-zinc-100' : 'bg-indigo-500',
                      )}
                      style={{ height: `${Math.max(heightPct, p.review_count === 0 ? 4 : 6)}%` }}
                    />
                  </div>
                  <span className="text-[9px] text-zinc-400">
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
    <Card>
      <CardHeader>
        <CardTitle>规则命中榜 Top 10</CardTitle>
        <CardDescription>按命中数降序；被删规则以 rule_id 保留。</CardDescription>
      </CardHeader>
      <CardContent>
        {rules.length === 0 ? (
          <div className="py-6 text-center text-[13px] text-zinc-500" data-testid="rules-empty">
            暂无问题
          </div>
        ) : (
          <div className="divide-y divide-zinc-100" role="list" aria-label="规则命中榜">
            <div className="grid grid-cols-[minmax(0,3fr)_60px_60px_60px] gap-3 py-2 text-[11px] font-medium uppercase text-zinc-400">
              <div>规则</div>
              <div className="text-right">命中</div>
              <div className="text-right">误报率</div>
              <div className="text-right">项目</div>
            </div>
            {rules.map((rule) => {
              const sev = severityDisplay(rule.severity_default);
              const cat = categoryDisplay(rule.category_default);
              const fp = fpRateBucket(rule.fp_rate);
              return (
                <div
                  key={rule.rule_id}
                  role="listitem"
                  data-testid="rule-row"
                  data-rule-id={rule.rule_id}
                  className="grid grid-cols-[minmax(0,3fr)_60px_60px_60px] items-center gap-3 py-2 text-[13px]"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span title={sev.label}>{sev.emoji}</span>
                      <span className="truncate font-mono text-[12px] text-zinc-600">
                        {rule.rule_id}
                      </span>
                      <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-500">
                        {cat.emoji} {cat.label}
                      </span>
                    </div>
                    <div className="truncate text-[12px] text-zinc-500">
                      {rule.title ?? '（规则已删除）'}
                    </div>
                  </div>
                  <div className="text-right font-semibold text-zinc-900">
                    {rule.finding_count}
                  </div>
                  <div className="text-right">
                    <span className={cn('rounded px-1.5 py-0.5 text-[11px]', fp.className)}>
                      {fp.label}
                    </span>
                  </div>
                  <div className="text-right text-zinc-500">{rule.projects_hit}</div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------- 项目活跃度 Top 10 ----------------

function ProjectsSection(props: { projects: ProjectStat[] }): React.ReactElement {
  const { projects } = props;
  return (
    <Card>
      <CardHeader>
        <CardTitle>项目活跃度 Top 10</CardTitle>
        <CardDescription>按审查数降序。</CardDescription>
      </CardHeader>
      <CardContent>
        {projects.length === 0 ? (
          <div className="py-6 text-center text-[13px] text-zinc-500" data-testid="projects-empty">
            暂无项目
          </div>
        ) : (
          <div className="divide-y divide-zinc-100" role="list" aria-label="项目活跃度">
            <div className="grid grid-cols-[minmax(0,3fr)_60px_60px_60px_100px_120px] gap-3 py-2 text-[11px] font-medium uppercase text-zinc-400">
              <div>项目</div>
              <div className="text-right">审查</div>
              <div className="text-right">问题</div>
              <div className="text-right">BLOCKER</div>
              <div className="text-right">平均耗时</div>
              <div className="text-right">最后审查</div>
            </div>
            {projects.map((p) => (
              <div
                key={p.project_id}
                role="listitem"
                data-testid="project-row"
                data-project-id={p.project_id}
                className="grid grid-cols-[minmax(0,3fr)_60px_60px_60px_100px_120px] items-center gap-3 py-2 text-[13px]"
              >
                <div className="truncate text-zinc-900">{p.project_name}</div>
                <div className="text-right font-semibold">{p.review_count}</div>
                <div className="text-right">{p.finding_count}</div>
                <div className="text-right text-red-600">{p.blocker_count}</div>
                <div className="text-right text-zinc-500">
                  {p.avg_duration_ms != null ? `${(p.avg_duration_ms / 1000).toFixed(2)}s` : '—'}
                </div>
                <div className="text-right text-zinc-500">
                  {relativeTime(p.last_reviewed_at ?? undefined) || '—'}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------- 分类分布 ----------------

function CategoriesSection(props: { categories: CategoryStat[] }): React.ReactElement {
  const { categories } = props;
  const max = Math.max(1, ...categories.map((c) => c.count));
  return (
    <Card>
      <CardHeader>
        <CardTitle>分类分布</CardTitle>
        <CardDescription>未分类问题归入「其他」。</CardDescription>
      </CardHeader>
      <CardContent>
        {categories.length === 0 ? (
          <div className="py-6 text-center text-[13px] text-zinc-500" data-testid="categories-empty">
            暂无问题
          </div>
        ) : (
          <div className="space-y-2" role="list" aria-label="分类分布">
            {categories.map((c) => {
              const disp = categoryDisplay(c.category);
              const widthPct = Math.round((c.count / max) * 100);
              const percentageLabel = `${(c.percentage * 100).toFixed(1)}%`;
              return (
                <div
                  key={c.category}
                  role="listitem"
                  data-testid="category-row"
                  data-category={c.category}
                  className="flex items-center gap-3 text-[13px]"
                >
                  <div className="w-24 shrink-0 truncate">
                    <span className="mr-1">{disp.emoji}</span>
                    <span className="text-zinc-700">{disp.label}</span>
                  </div>
                  <div className="relative h-2 flex-1 rounded-full bg-zinc-100">
                    <div
                      className="absolute inset-y-0 left-0 rounded-full bg-indigo-400"
                      style={{ width: `${widthPct}%` }}
                    />
                  </div>
                  <div className="w-32 shrink-0 text-right text-zinc-500 tabular-nums">
                    <span className="font-semibold text-zinc-900">{c.count}</span>
                    <span className="ml-1 text-[12px] text-zinc-400">({percentageLabel})</span>
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
