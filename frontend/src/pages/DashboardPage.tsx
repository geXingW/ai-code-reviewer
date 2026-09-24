/**
 * 「仪表盘」页：系统级 KPI + 组件健康度 + 最近审查 + 统计图表。
 *
 * 假功能修复：「阻断问题」「发现问题」两个 KPI 此前只统计已加载的前 20 条
 * 审查记录（useMemo 遍历 reviewRecordsPage.items）。现在全部来自
 * /api/stats/overview 的全量聚合（total_reviews / total_blockers /
 * total_findings / fp_pending），时间窗口可切换 7 / 30 / 90 天。
 */

import { useEffect, useState } from 'react';
import { Alert, Card, Segmented, Skeleton } from 'antd';

import {
  fetchEngines,
  fetchRecentReviews,
  fetchStatsCategories,
  fetchStatsOverview,
  fetchStatsProjects,
  fetchStatsRules,
  fetchStatsTimeseries,
  type CategoryStat,
  type EngineSummary,
  type HealthStatus,
  type ProjectStat,
  type RecentReview,
  type RuleStat,
  type StatsOverview,
  type TimeseriesPoint,
} from '../api';
import { relativeTime } from '../lib/format';
import { categoryDisplay, severityDisplay } from '../lib/findingTaxonomy';
import {
  LifecycleEventTag,
  ReviewModeTag,
  ReviewStatusTag,
  StatusDot,
} from '../components/entityTags';

export interface DashboardPageProps {
  health: HealthStatus | null;
}

type StatsBundle = {
  overview: StatsOverview | null;
  rules: RuleStat[];
  projects: ProjectStat[];
  categories: CategoryStat[];
  timeseries: TimeseriesPoint[];
};

const EMPTY_STATS_BUNDLE: StatsBundle = {
  overview: null,
  rules: [],
  projects: [],
  categories: [],
  timeseries: [],
};

export function DashboardPage({ health }: DashboardPageProps) {
  const [engines, setEngines] = useState<EngineSummary[]>([]);
  const [recentReviews, setRecentReviews] = useState<RecentReview[]>([]);
  const [statsDays, setStatsDays] = useState<number>(30);
  const [statsBundle, setStatsBundle] = useState<StatsBundle>(EMPTY_STATS_BUNDLE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const [recent, overview, rules, projects, categories, timeseries] = await Promise.all([
          fetchRecentReviews(),
          fetchStatsOverview(statsDays),
          fetchStatsRules(statsDays, 10),
          fetchStatsProjects(statsDays, 10),
          fetchStatsCategories(statsDays),
          fetchStatsTimeseries(statsDays),
        ]);
        if (active) {
          setRecentReviews(recent.slice(0, 5));
          setStatsBundle({ overview, rules, projects, categories, timeseries });
        }
      } catch (caught) {
        if (active) {
          setError(caught instanceof Error ? caught.message : '加载统计数据失败');
        }
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [statsDays]);

  const overview = statsBundle.overview;

  return (
    <div className="space-y-5" aria-busy={loading}>
      {error ? <Alert type="error" showIcon message={error} /> : null}

      {/* ========== 第一行：系统级 KPI（全量统计口径，来自 /api/stats/overview） ========== */}
      <section>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <KpiCard label={`总审查（近 ${statsDays} 天）`} value={overview?.total_reviews ?? '—'} />
          <KpiCard
            label={`阻断问题（近 ${statsDays} 天）`}
            value={overview?.total_blockers ?? '—'}
            intent="danger"
          />
          <KpiCard label={`发现问题（近 ${statsDays} 天）`} value={overview?.total_findings ?? '—'} />
          <KpiCard label="待处理误报" value={overview?.fp_pending ?? '—'} />
        </div>
      </section>

      {/* ========== 第二行：系统状态 + 最近审查 ========== */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <SystemStatusCard health={health} engines={engines} />
        <RecentReviewsPanel reviews={recentReviews} />
      </section>

      {/* ========== 第三行：统计时间窗口 ========== */}
      <section>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-[15px] font-semibold text-zinc-900">数据统计</h2>
          <Segmented
            aria-label="统计时间窗口"
            value={statsDays}
            onChange={(value) => setStatsDays(value as number)}
            options={[
              { value: 7, label: '最近 7 天' },
              { value: 30, label: '最近 30 天' },
              { value: 90, label: '最近 90 天' },
            ]}
          />
        </div>
      </section>

      {/* ========== 第四行：时间趋势 + 问题分类分布 ========== */}
      <section className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card title="审查趋势" styles={{ body: { paddingTop: 8 } }}>
          {loading ? (
            <Skeleton active />
          ) : statsBundle.timeseries.length === 0 ? (
            <div className="py-8 text-center text-[13px] text-zinc-400">暂无时间序列数据</div>
          ) : (
            <div>
              <div className="mb-2 flex items-center gap-4 text-[11px] text-zinc-500">
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded-sm bg-zinc-800" />
                  审查量
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-rose-500 ring-2 ring-white" />
                  含 BLOCKER
                </span>
              </div>
              <TrendChart data={statsBundle.timeseries} />
            </div>
          )}
        </Card>

        <Card title="问题分布" styles={{ body: { paddingTop: 8 } }}>
          {loading ? (
            <Skeleton active />
          ) : statsBundle.categories.length === 0 ? (
            <div className="py-8 text-center text-[13px] text-zinc-400">暂无分类数据</div>
          ) : (
            <div className="space-y-2">
              {statsBundle.categories.map((cat) => {
                const cd = categoryDisplay(cat.category);
                const total = statsBundle.categories.reduce((sum, c) => sum + c.count, 0);
                const pct = total > 0 ? Math.round((cat.count / total) * 100) : 0;
                return (
                  <div key={cat.category} className="space-y-1">
                    <div className="flex items-center justify-between text-[12px]">
                      <span className="flex items-center gap-1.5 text-zinc-700">
                        <span>{cd.emoji}</span>
                        <span className="truncate">{cd.label}</span>
                      </span>
                      <span className="font-semibold text-zinc-900">{cat.count}</span>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-100">
                      <div
                        className="h-full rounded-full bg-zinc-800 transition-all"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </section>

      {/* ========== 第五行：规则命中榜 + 项目活跃度 ========== */}
      <section className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <Card title="高频规则 Top 8" styles={{ body: { paddingTop: 8 } }}>
          {loading ? (
            <Skeleton active />
          ) : statsBundle.rules.length === 0 ? (
            <div className="py-8 text-center text-[13px] text-zinc-400">暂无问题</div>
          ) : (
            <div className="divide-y divide-zinc-100">
              <div className="grid grid-cols-[minmax(0,2fr)_40px_50px] gap-2 py-1.5 text-[10px] font-medium uppercase text-zinc-400">
                <div>规则</div>
                <div className="text-right">命中</div>
                <div className="text-right">误报</div>
              </div>
              {statsBundle.rules.slice(0, 8).map((rule) => {
                const sev = severityDisplay(rule.severity_default);
                const pct = Math.round(rule.fp_rate * 100);
                return (
                  <div
                    key={rule.rule_id}
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
                    <div className="text-right font-semibold text-zinc-900">{rule.finding_count}</div>
                    <div className="text-right">
                      <span
                        className={`rounded px-1 py-0.5 text-[10px] ${
                          pct >= 30
                            ? 'bg-red-100 text-red-700'
                            : pct >= 10
                              ? 'bg-yellow-100 text-yellow-800'
                              : 'bg-zinc-100 text-zinc-600'
                        }`}
                      >
                        {pct}%
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card title="活跃项目 Top 6" styles={{ body: { paddingTop: 8 } }}>
          {loading ? (
            <Skeleton active />
          ) : statsBundle.projects.length === 0 ? (
            <div className="py-8 text-center text-[13px] text-zinc-400">暂无项目</div>
          ) : (
            <div className="divide-y divide-zinc-100">
              <div className="grid grid-cols-[minmax(0,1.5fr)_35px_35px] gap-2 py-1.5 text-[10px] font-medium uppercase text-zinc-400">
                <div>项目</div>
                <div className="text-right">审查</div>
                <div className="text-right">问题</div>
              </div>
              {statsBundle.projects.slice(0, 6).map((p) => (
                <div
                  key={p.project_id}
                  className="grid grid-cols-[minmax(0,1.5fr)_35px_35px] items-center gap-2 py-1.5 text-[12px]"
                >
                  <div className="truncate text-zinc-700">{p.project_name}</div>
                  <div className="text-right font-semibold text-zinc-900">{p.review_count}</div>
                  <div className="text-right">
                    <span className={p.blocker_count > 0 ? 'font-medium text-rose-600' : 'text-zinc-500'}>
                      {p.finding_count}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}

type KpiCardProps = {
  label: string;
  value: number | string;
  intent?: 'neutral' | 'danger';
};

function KpiCard({ label, value, intent = 'neutral' }: KpiCardProps) {
  const danger = intent === 'danger' && Number(value) > 0;
  return (
    <Card styles={{ body: { padding: 16 } }}>
      <div className="text-[12px] text-zinc-500">{label}</div>
      <div
        className={`mt-2 text-[24px] font-semibold leading-none tracking-tight ${
          danger ? 'text-rose-600' : 'text-zinc-900'
        }`}
      >
        {value}
      </div>
    </Card>
  );
}

type SystemStatusCardProps = {
  health: HealthStatus | null;
  engines: EngineSummary[];
};

function SystemStatusCard({ health, engines }: SystemStatusCardProps) {
  const apiOk = health?.status === 'ok';
  const dbOk = health?.db === 'ok';
  const healthyEngineCount = engines.filter((engine) => engine.healthy).length;
  const enginesAllHealthy = engines.length > 0 && healthyEngineCount === engines.length;
  const enginesAllDown = engines.length > 0 && healthyEngineCount === 0;
  const engineTone =
    engines.length === 0 ? 'idle' : enginesAllHealthy ? 'ok' : enginesAllDown ? 'bad' : 'warn';
  const allOk = apiOk && dbOk && (engines.length === 0 || enginesAllHealthy);

  return (
    <Card
      title="系统状态"
      extra={
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ${
            allOk ? 'bg-emerald-50 text-emerald-600' : 'bg-rose-50 text-rose-600'
          }`}
        >
          <StatusDot tone={allOk ? 'ok' : 'bad'} />
          {allOk ? '正常' : '异常'}
        </span>
      }
      styles={{ body: { paddingTop: 0 } }}
    >
      <div className="divide-y divide-zinc-100">
        <div className="flex items-center justify-between py-2">
          <span className="flex items-center gap-2 text-[13px] text-zinc-700">
            <StatusDot tone={apiOk ? 'ok' : 'bad'} />
            API 服务
          </span>
          <span className="text-[11px] text-zinc-500">
            {apiOk ? '服务正常' : '服务异常'}
            {health?.version ? <span className="ml-1.5 font-mono text-zinc-400">v{health.version}</span> : null}
          </span>
        </div>
        <div className="flex items-center justify-between py-2">
          <span className="flex items-center gap-2 text-[13px] text-zinc-700">
            <StatusDot tone={dbOk ? 'ok' : 'bad'} />
            数据库
          </span>
          <span className="text-[11px] text-zinc-500">{dbOk ? '数据库正常' : '数据库异常'}</span>
        </div>
        <div className="flex items-center justify-between py-2">
          <span className="flex min-w-0 items-center gap-2 text-[13px] text-zinc-700">
            <StatusDot tone={engineTone} />
            <span className="shrink-0">引擎</span>
            <span className="truncate">
              {engines.length > 0 ? engines.map((engine) => engine.name).join(' / ') : '—'}
            </span>
          </span>
          <span className="ml-2 shrink-0 font-mono text-[11px] text-zinc-500">
            {healthyEngineCount}/{engines.length}
          </span>
        </div>
      </div>
    </Card>
  );
}

type RecentReviewsPanelProps = {
  reviews: RecentReview[];
};

function RecentReviewsPanel({ reviews }: RecentReviewsPanelProps) {
  return (
    <Card title="最近审查" styles={{ body: { paddingTop: 0 } }}>
      {reviews.length === 0 ? (
        <div className="py-6 text-center text-[13px] text-zinc-400">
          暂无审查记录 · 当 GitLab MR 触发审查后，记录会显示在这里
        </div>
      ) : (
        <div className="divide-y divide-zinc-100">
          {reviews.map((review) => (
            <div
              key={`${review.review_id ?? review.project_id}-${review.mr_iid}`}
              className="flex items-center gap-3 py-2.5"
            >
              <StatusDot tone={review.has_blocker ? 'bad' : 'ok'} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[13px] font-medium text-zinc-900">
                    {review.title || `MR !${review.mr_iid}`}
                  </span>
                  <span className="shrink-0 rounded bg-zinc-100 px-1.5 text-[10px] font-mono text-zinc-500">
                    !{review.mr_iid}
                  </span>
                  {review.engine_used ? (
                    <span className="shrink-0 rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 text-[10px] font-mono text-zinc-600">
                      {review.engine_used}
                    </span>
                  ) : null}
                </div>
                <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
                  {review.project_path} · {relativeTime(review.created_at)}
                </div>
              </div>
              <ReviewStatusTag status={review.status} hasBlocker={review.has_blocker} />
              {review.lifecycle_event ? (
                <LifecycleEventTag event={review.lifecycle_event} />
              ) : review.review_mode && review.review_mode !== 'full' ? (
                <ReviewModeTag mode={review.review_mode} />
              ) : null}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

/** SVG 面积趋势图（参考 Vercel Analytics 风格），数据来自 /api/stats/timeseries。 */
function TrendChart({ data }: { data: TimeseriesPoint[] }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const W = 640;
  const H = 160;
  const P = { top: 12, right: 12, bottom: 28, left: 32 };
  const cw = W - P.left - P.right;
  const ch = H - P.top - P.bottom;
  const max = Math.max(1, ...data.map((d) => d.review_count));
  const step = data.length > 1 ? cw / (data.length - 1) : cw;
  const pts = data.map((d, i) => ({
    x: P.left + i * step,
    y: P.top + ch - (d.review_count / max) * ch,
    d,
  }));
  const areaPath =
    pts.length > 0
      ? `M ${pts[0].x} ${P.top + ch} ` +
        pts.map((p) => `L ${p.x} ${p.y}`).join(' ') +
        ` L ${pts[pts.length - 1].x} ${P.top + ch} Z`
      : '';
  const linePath = pts.length > 0 ? pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ') : '';
  const yTicks = [0, Math.ceil(max / 2), max];
  const labelInterval = data.length <= 7 ? 1 : data.length <= 30 ? 5 : 10;

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 'auto' }}>
        <defs>
          <linearGradient id="trendArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#27272A" stopOpacity={0.12} />
            <stop offset="100%" stopColor="#27272A" stopOpacity={0} />
          </linearGradient>
        </defs>
        {yTicks.map((tick, i) => {
          const y = P.top + ch - (tick / max) * ch;
          return (
            <g key={i}>
              <line x1={P.left} y1={y} x2={W - P.right} y2={y} stroke="#F4F4F5" strokeWidth={1} />
              <text x={P.left - 6} y={y + 3} textAnchor="end" fontSize={9} fill="#A1A1AA">
                {tick}
              </text>
            </g>
          );
        })}
        <path d={areaPath} fill="url(#trendArea)" />
        <path
          d={linePath}
          fill="none"
          stroke="#27272A"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {pts
          .filter((p) => p.d.blocker_count > 0)
          .map((p, i) => (
            <circle
              key={`b-${i}`}
              cx={p.x}
              cy={p.y}
              r={3.5}
              fill="#EF4444"
              stroke="white"
              strokeWidth={1.5}
            />
          ))}
        {hovered !== null && pts[hovered] && (
          <g>
            <line
              x1={pts[hovered].x}
              y1={P.top}
              x2={pts[hovered].x}
              y2={P.top + ch}
              stroke="#D4D4D8"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <circle
              cx={pts[hovered].x}
              cy={pts[hovered].y}
              r={4}
              fill="#27272A"
              stroke="white"
              strokeWidth={2}
            />
          </g>
        )}
        {data.map((d, i) => {
          if (i % labelInterval !== 0 && i !== data.length - 1) return null;
          const x = P.left + i * step;
          return (
            <text
              key={`l-${i}`}
              x={x}
              y={H - 8}
              textAnchor="middle"
              fontSize={9}
              fill="#A1A1AA"
              fontFamily="monospace"
            >
              {d.date.slice(5)}
            </text>
          );
        })}
        {pts.map((p, i) => (
          <rect
            key={`h-${i}`}
            x={p.x - step / 2}
            y={0}
            width={step}
            height={H}
            fill="transparent"
            onMouseEnter={() => setHovered(i)}
            onMouseLeave={() => setHovered(null)}
          />
        ))}
      </svg>
      {hovered !== null && data[hovered] && (
        <div
          className="pointer-events-none absolute top-0 z-10 rounded-md border border-zinc-200 bg-white px-2 py-1 text-[11px] shadow-md"
          style={{ left: `${(pts[hovered].x / W) * 100}%`, transform: 'translateX(-50%)' }}
        >
          <div className="font-mono text-zinc-900">{data[hovered].date}</div>
          <div className="text-zinc-500">
            评审 {data[hovered].review_count} 次 · 发现 {data[hovered].finding_count} 个问题 · 其中{' '}
            {data[hovered].blocker_count} 个 BLOCKER
          </div>
        </div>
      )}
    </div>
  );
}
