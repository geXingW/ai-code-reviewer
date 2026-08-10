import * as React from 'react';
import { ChevronDown } from 'lucide-react';

import { cn } from '@/lib/utils';

/**
 * Select 组件：Linear 风格浅色下拉框，与 Input 保持一致的视觉规格
 * （32px 高 / 13px 字号 / 6px 圆角 / #E4E4E7 边框）。
 *
 * 原生 `<select>` 外层包一层 relative div，右侧叠加 ChevronDown 图标，
 * 并用 appearance-none 隐藏原生箭头。保留原生 `<select>` 以保证可访问性
 * 与键盘导航。
 */
export type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement>;

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div className="relative">
        <select
          ref={ref}
          className={cn(
            'flex h-8 w-full appearance-none rounded-md border border-[#E4E4E7] bg-white pl-3 pr-9 py-1 text-[13px] text-foreground',
            'hover:border-[#D4D4D8]',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-0',
            'disabled:cursor-not-allowed disabled:opacity-50',
            className,
          )}
          {...props}
        >
          {children}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-zinc-400"
          aria-hidden
        />
      </div>
    );
  },
);
Select.displayName = 'Select';
