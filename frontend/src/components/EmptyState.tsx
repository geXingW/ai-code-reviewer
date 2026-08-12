import * as React from 'react';
import { type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * 空状态组件（参考 Linear / Vercel 风格）。
 * 居中展示图标 + 标题 + 描述 + 可选操作按钮。
 */
interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center gap-2 px-6 py-10 text-center', className)}>
      <div className="flex size-10 items-center justify-center rounded-lg bg-zinc-50">
        <Icon size={20} strokeWidth={1.5} className="text-zinc-300" />
      </div>
      <div className="text-[13px] font-medium text-zinc-600">{title}</div>
      {description ? (
        <div className="text-[12px] text-zinc-400">{description}</div>
      ) : null}
      {action ? (
        <div className="mt-2">{action}</div>
      ) : null}
    </div>
  );
}
