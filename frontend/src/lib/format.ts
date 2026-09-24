/**
 * 通用展示格式化工具（从 App.tsx 抽出，供各页面与弹窗共用）。
 */

export function relativeTime(iso?: string): string {
  if (!iso) {
    return '';
  }
  // 防守：后端理论上已保证输出带 tz（AwareDatetime），这里再补一层兜底——
  // 如果字符串既没有 Z 也没有 +HH:MM / -HH:MM，就当作 UTC 追加 Z，
  // 避免浏览器按本地时区解析导致时差错位（见 backend/app/schemas/_datetime.py）。
  const hasTz = /[Zz]$/.test(iso) || /[+-]\d{2}:?\d{2}$/.test(iso);
  const normalized = hasTz ? iso : `${iso}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) {
    return '';
  }
  const diffSeconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (diffSeconds < 60) {
    return '刚刚';
  }
  const minutes = Math.floor(diffSeconds / 60);
  if (minutes < 60) {
    return `${minutes} 分钟前`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} 小时前`;
  }
  const days = Math.floor(hours / 24);
  if (days < 30) {
    return `${days} 天前`;
  }
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function truncate(text: string, limit = 80): string {
  const trimmed = text.trim();
  return trimmed.length > limit ? `${trimmed.slice(0, limit)}…` : trimmed;
}

export function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return '未知错误';
}

/** 格式化毫秒耗时：mm:ss（不足 1 分钟显示秒，超过 1 小时显示 h:mm:ss）。 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) {
    return '—';
  }
  const totalSeconds = Math.round(ms / 1000);
  const seconds = totalSeconds % 60;
  const minutes = Math.floor(totalSeconds / 60) % 60;
  const hours = Math.floor(totalSeconds / 3600);
  const pad = (n: number) => String(n).padStart(2, '0');
  if (hours > 0) {
    return `${hours}:${pad(minutes)}:${pad(seconds)}`;
  }
  return `${minutes}:${pad(seconds)}`;
}
