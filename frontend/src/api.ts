export type HealthStatus = {
  status: string;
  version: string;
  db: string;
  redis: string;
};

export type EngineSummary = {
  name: string;
  supports_feedback: boolean;
  requires_repo_clone: boolean;
  healthy: boolean;
  health_status: string;
};

export type RecentReview = {
  review_id: string | null;
  project_id: number;
  project_path: string;
  mr_iid: number;
  title: string;
  web_url: string | null;
  status: string;
  has_blocker: boolean;
  finding_count: number;
  blocker_count: number;
  policy_applied: string | null;
  review_url: string | null;
  created_at?: string;
};

export type CreateReviewPayload = {
  project_id: number;
  mr_iid: number;
  target_branch: string;
  source_branch: string;
  commit_sha: string;
  project_path?: string;
  title?: string;
  web_url?: string;
};

export type CreateReviewResponse = {
  review_id: string | null;
  status: string;
  has_blocker: boolean;
  finding_count: number;
  blocker_count: number;
  policy_applied: string | null;
  review_url: string | null;
};

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T | { detail?: string };
  if (!response.ok) {
    const detail = typeof (payload as { detail?: string }).detail === 'string'
      ? (payload as { detail: string }).detail
      : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload as T;
}

export async function fetchHealth(): Promise<HealthStatus> {
  const response = await fetch('/health');
  return parseJsonResponse<HealthStatus>(response);
}

export async function fetchEngines(): Promise<EngineSummary[]> {
  const response = await fetch('/api/engines');
  return parseJsonResponse<EngineSummary[]>(response);
}

export async function fetchRecentReviews(internalToken: string): Promise<RecentReview[]> {
  const response = await fetch('/api/reviews/recent', {
    headers: {
      'X-Internal-Token': internalToken,
    },
  });
  return parseJsonResponse<RecentReview[]>(response);
}

export async function createReview(
  payload: CreateReviewPayload,
  internalToken: string,
): Promise<CreateReviewResponse> {
  const response = await fetch('/api/reviews', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Internal-Token': internalToken,
    },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<CreateReviewResponse>(response);
}
