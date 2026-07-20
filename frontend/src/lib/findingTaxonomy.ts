/**
 * Finding 展示元数据：severity / category → emoji + label。
 * 与后端 backend/app/core/finding_taxonomy.py 保持字段与语义一致。
 *
 * 前端不做 rule_id → category 推断（那是后端渲染 GitLab 评论的责任）；
 * 规则关联页只需要展示 rule.severity_default / rule.category_default 的
 * 视觉标签，无 finding 上下文。
 */

export type Severity = 'BLOCKER' | 'WARNING' | 'INFO';

export type FindingCategory =
  | 'security'
  | 'bug'
  | 'performance'
  | 'maintainability'
  | 'style'
  | 'other';

export const SEVERITY_DISPLAY: Record<Severity, { emoji: string; label: string }> = {
  BLOCKER: { emoji: '🔴', label: 'BLOCKER' },
  WARNING: { emoji: '🟡', label: 'WARNING' },
  INFO: { emoji: '🔵', label: 'INFO' },
};

export const CATEGORY_DISPLAY: Record<FindingCategory, { emoji: string; label: string }> = {
  security: { emoji: '🔒', label: '安全' },
  bug: { emoji: '🐛', label: '缺陷' },
  performance: { emoji: '⚡', label: '性能' },
  maintainability: { emoji: '🔧', label: '可维护性' },
  style: { emoji: '🎨', label: '风格' },
  other: { emoji: '📝', label: '其他' },
};

export const SEVERITY_ORDER: readonly Severity[] = ['BLOCKER', 'WARNING', 'INFO'];

export const CATEGORY_ORDER: readonly FindingCategory[] = [
  'security',
  'bug',
  'performance',
  'maintainability',
  'style',
  'other',
];

/** 未知 severity 用中性圆点兜底，保持渲染鲁棒性。 */
export function severityDisplay(value: string | null | undefined): { emoji: string; label: string } {
  if (value && (SEVERITY_ORDER as readonly string[]).includes(value.toUpperCase())) {
    return SEVERITY_DISPLAY[value.toUpperCase() as Severity];
  }
  return { emoji: '⚪', label: (value ?? '').toUpperCase() };
}

/** 未知 / 缺失 category 走 'other'，与后端一致。 */
export function categoryDisplay(value: string | null | undefined): { emoji: string; label: string } {
  if (value && (CATEGORY_ORDER as readonly string[]).includes(value)) {
    return CATEGORY_DISPLAY[value as FindingCategory];
  }
  return CATEGORY_DISPLAY.other;
}

/** 兜底判断字符串是否为合法 severity，用于筛选逻辑。 */
export function isKnownSeverity(value: string | null | undefined): value is Severity {
  return !!value && (SEVERITY_ORDER as readonly string[]).includes(value.toUpperCase());
}

export function isKnownCategory(value: string | null | undefined): value is FindingCategory {
  return !!value && (CATEGORY_ORDER as readonly string[]).includes(value);
}
