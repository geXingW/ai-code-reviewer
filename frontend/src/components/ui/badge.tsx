import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

/**
 * Badge 组件（Linear 风格）：浅底 + 深字 + 同色浅边框，高 20px / 字号 11px。
 *   - default     → neutral（灰）
 *   - success     → emerald
 *   - destructive → rose（error）
 *   - secondary   → indigo
 *   - outline     → 透明底 + 边框
 */
const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded border px-2 h-5 text-[11px] font-medium transition-colors',
  {
    variants: {
      variant: {
        default: 'border-zinc-200 bg-zinc-100 text-zinc-600',
        success: 'border-emerald-100 bg-emerald-50 text-emerald-700',
        destructive: 'border-rose-100 bg-rose-50 text-rose-700',
        secondary: 'border-indigo-100 bg-indigo-50 text-indigo-700',
        outline: 'border-zinc-200 bg-transparent text-zinc-600',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
export { badgeVariants };
