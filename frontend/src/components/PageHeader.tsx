import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * 统一页面头部（参考 Linear / Vercel Dashboard）。
 * 左侧标题 + 描述，右侧操作按钮区。
 */
interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  className?: string;
}

export function PageHeader({ title, description, actions, className }: PageHeaderProps) {
  return (
    <div className={cn('flex items-start justify-between gap-4 pb-4', className)}>
      <div className="min-w-0">
        <h1 className="text-[18px] font-semibold leading-tight text-zinc-900">{title}</h1>
        {description ? (
          <p className="mt-1 text-[13px] text-zinc-500">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}
