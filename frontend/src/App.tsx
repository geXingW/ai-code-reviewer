import { FormEvent, useEffect, useMemo, useState } from 'react';

import {
  CreateReviewPayload,
  CreateReviewResponse,
  EngineSummary,
  HealthStatus,
  RecentReview,
  createReview,
  fetchEngines,
  fetchHealth,
  fetchRecentReviews,
} from './api';

type FormState = {
  internalToken: string;
  projectId: string;
  mrIid: string;
  targetBranch: string;
  sourceBranch: string;
  commitSha: string;
  projectPath: string;
  title: string;
  webUrl: string;
};

const initialForm: FormState = {
  internalToken: '',
  projectId: '',
  mrIid: '',
  targetBranch: '',
  sourceBranch: '',
  commitSha: '',
  projectPath: '',
  title: '',
  webUrl: '',
};

function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [engines, setEngines] = useState<EngineSummary[]>([]);
  const [reviews, setReviews] = useState<RecentReview[]>([]);
  const [form, setForm] = useState<FormState>(initialForm);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitResult, setSubmitResult] = useState<CreateReviewResponse | null>(null);

  useEffect(() => {
    let active = true;

    async function loadDashboard() {
      try {
        setLoading(true);
        setError(null);
        const [nextHealth, nextEngines] = await Promise.all([
          fetchHealth(),
          fetchEngines(),
        ]);
        if (active) {
          setHealth(nextHealth);
          setEngines(nextEngines);
        }
      } catch (caught) {
        if (active) {
          setError(toErrorMessage(caught));
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void loadDashboard();
    return () => {
      active = false;
    };
  }, []);

  const blockerReviews = useMemo(
    () => reviews.filter((review) => review.has_blocker).length,
    [reviews],
  );

  async function handleRefreshReviews() {
    setError(null);
    try {
      const token = form.internalToken.trim();
      if (!token) {
        throw new Error('内部调用 Token 不能为空。');
      }
      setReviews(await fetchRecentReviews(token));
    } catch (caught) {
      setError(toErrorMessage(caught));
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setSubmitResult(null);
    setError(null);

    try {
      const payload: CreateReviewPayload = {
        project_id: parsePositiveInteger(form.projectId, 'GitLab 项目 ID'),
        mr_iid: parsePositiveInteger(form.mrIid, 'MR IID'),
        target_branch: form.targetBranch.trim(),
        source_branch: form.sourceBranch.trim(),
        commit_sha: form.commitSha.trim(),
      };
      if (form.projectPath.trim()) {
        payload.project_path = form.projectPath.trim();
      }
      if (form.title.trim()) {
        payload.title = form.title.trim();
      }
      if (form.webUrl.trim()) {
        payload.web_url = form.webUrl.trim();
      }
      if (!form.internalToken.trim()) {
        throw new Error('内部调用 Token 不能为空。');
      }
      if (!payload.target_branch || !payload.source_branch || !payload.commit_sha) {
        throw new Error('目标分支、源分支和 Commit SHA 不能为空。');
      }

      const result = await createReview(payload, form.internalToken.trim());
      setSubmitResult(result);
      setReviews(await fetchRecentReviews(form.internalToken.trim()));
    } catch (caught) {
      setError(toErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="app-shell">
      <section className="hero">
        <div>
          <h1>AI Code Reviewer</h1>
          <p>GitLab MR AI 审查 MVP 管理台：看状态、看最近审查、手动触发联调。</p>
        </div>
        <div className="badge ok">MVP</div>
      </section>

      {error ? <div className="alert error" role="alert">{error}</div> : null}

      <section className="grid" aria-busy={loading}>
        <div className="card span-4">
          <h2>服务状态</h2>
          <StatusRow label="API" value={health?.status === 'ok' ? '服务正常' : '服务异常'} ok={health?.status === 'ok'} />
          <StatusRow label="数据库" value={health?.db === 'ok' ? '数据库正常' : '数据库异常'} ok={health?.db === 'ok'} />
          <StatusRow label="Redis" value={health?.redis === 'ok' ? 'Redis 正常' : 'Redis 异常'} ok={health?.redis === 'ok'} />
          <p className="muted">版本：{health?.version ?? '加载中'}</p>
        </div>

        <div className="card span-4">
          <h2>引擎状态</h2>
          {engines.length === 0 ? <div className="empty">暂无已注册引擎</div> : null}
          {engines.map((engine) => (
            <div className="engine-row" key={engine.name}>
              <div>
                <strong>{engine.name}</strong>
                <div className="muted">
                  {engine.requires_repo_clone ? '需要克隆仓库' : '无需克隆仓库'} ·
                  {engine.supports_feedback ? ' 支持反馈' : ' 暂不支持反馈'}
                </div>
              </div>
              <Badge ok={engine.healthy}>{engine.health_status}</Badge>
            </div>
          ))}
        </div>

        <div className="card span-4">
          <h2>审查概览</h2>
          <StatusRow label="最近审查" value={`${reviews.length} 次`} ok />
          <StatusRow label="存在阻断" value={`${blockerReviews} 次`} ok={blockerReviews === 0} />
          <StatusRow label="可手动触发" value="已启用" ok />
        </div>

        <div className="card span-12">
          <h2>手动触发 MR 审查</h2>
          <form className="form-grid" onSubmit={handleSubmit}>
            <TextInput label="内部调用 Token" value={form.internalToken} type="password" onChange={(value) => setForm({ ...form, internalToken: value })} />
            <TextInput label="GitLab 项目 ID" value={form.projectId} onChange={(value) => setForm({ ...form, projectId: value })} />
            <TextInput label="MR IID" value={form.mrIid} onChange={(value) => setForm({ ...form, mrIid: value })} />
            <TextInput label="目标分支" value={form.targetBranch} onChange={(value) => setForm({ ...form, targetBranch: value })} />
            <TextInput label="源分支" value={form.sourceBranch} onChange={(value) => setForm({ ...form, sourceBranch: value })} />
            <TextInput label="Commit SHA" value={form.commitSha} onChange={(value) => setForm({ ...form, commitSha: value })} />
            <TextInput label="项目路径（可选）" value={form.projectPath} onChange={(value) => setForm({ ...form, projectPath: value })} />
            <TextInput label="MR 标题（可选）" value={form.title} onChange={(value) => setForm({ ...form, title: value })} />
            <TextInput label="MR URL（可选）" value={form.webUrl} onChange={(value) => setForm({ ...form, webUrl: value })} />
            <div className="form-actions">
              <button disabled={submitting} type="submit">{submitting ? '审查中…' : '触发审查'}</button>
              <button disabled={submitting} type="button" onClick={handleRefreshReviews}>刷新最近审查</button>
              <span className="muted">Token 只在本次请求中使用，不会保存到前端状态之外。</span>
            </div>
          </form>
          {submitResult ? (
            <div className={submitResult.has_blocker ? 'alert error' : 'alert ok'}>
              {submitResult.has_blocker ? '审查完成，发现阻断问题。' : '审查完成，未发现阻断问题。'}
              {submitResult.review_url ? <> <a href={submitResult.review_url}>查看结果</a></> : null}
            </div>
          ) : null}
        </div>

        <div className="card span-12">
          <h2>最近审查记录</h2>
          {reviews.length === 0 ? <div className="empty">暂无审查记录</div> : null}
          {reviews.map((review) => (
            <article className="review-row" key={`${review.review_id ?? review.project_id}-${review.mr_iid}`}>
              <div>
                <div className="review-title">{review.title}</div>
                <div className="review-meta">
                  {review.project_path} !{review.mr_iid} · {review.status} · {review.finding_count} 个问题
                </div>
              </div>
              <div>
                <Badge ok={!review.has_blocker}>{review.has_blocker ? '阻断' : '通过'}</Badge>
                {review.review_url ? <> <a href={review.review_url}>结果</a></> : null}
              </div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}

type TextInputProps = {
  label: string;
  value: string;
  type?: string;
  onChange: (value: string) => void;
};

function TextInput({ label, value, type = 'text', onChange }: TextInputProps) {
  return (
    <label>
      {label}
      <input type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

type StatusRowProps = {
  label: string;
  value: string;
  ok: boolean;
};

function StatusRow({ label, value, ok }: StatusRowProps) {
  return (
    <div className="status-row">
      <span className="muted">{label}</span>
      <Badge ok={ok}>{value}</Badge>
    </div>
  );
}

type BadgeProps = {
  ok: boolean;
  children: string;
};

function Badge({ ok, children }: BadgeProps) {
  return <span className={`badge ${ok ? 'ok' : 'error'}`}>{children}</span>;
}

function parsePositiveInteger(value: string, label: string): number {
  const numberValue = Number(value);
  if (!Number.isInteger(numberValue) || numberValue <= 0) {
    throw new Error(`${label} 必须是正整数。`);
  }
  return numberValue;
}

function toErrorMessage(caught: unknown): string {
  if (caught instanceof Error) {
    return caught.message;
  }
  return '请求失败，请稍后重试。';
}

export default App;
