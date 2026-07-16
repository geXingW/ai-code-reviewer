import { describe, expect, it, vi, afterEach } from 'vitest';

import { relativeTime } from './App';

afterEach(() => {
  vi.useRealTimers();
});

describe('relativeTime tz fallback', () => {
  it('把不带时区的 ISO 视为 UTC，避免按本地时区解析产生错位', () => {
    // 冻结 "现在"：UTC 2026-07-16T06:20:00Z
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-16T06:20:00Z'));

    // 后端漏了 tz 的 ISO：应被解析为 UTC，与当前只差 10 分钟
    const noTz = '2026-07-16T06:10:00';
    expect(relativeTime(noTz)).toBe('10 分钟前');
  });

  it('带 Z 的 ISO 保留原本 UTC 解析', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-16T06:20:00Z'));

    expect(relativeTime('2026-07-16T06:10:00Z')).toBe('10 分钟前');
  });

  it('带 +HH:MM 偏移的 ISO 保留原偏移', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-16T06:20:00Z'));

    // 14:10 +08:00 = 06:10 UTC，与 06:20 UTC 差 10 分钟
    expect(relativeTime('2026-07-16T14:10:00+08:00')).toBe('10 分钟前');
  });

  it('空输入返回空串', () => {
    expect(relativeTime(undefined)).toBe('');
    expect(relativeTime('')).toBe('');
  });
});
