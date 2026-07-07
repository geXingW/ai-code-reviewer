import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * Input 组件：Linear 风格浅色输入框（32px 高 / 13px 字号 / 6px 圆角）。
 * 边框 #E4E4E7，hover #D4D4D8，focus 由 Indigo 半透 ring 接管。
 */
export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type = 'text', ...props }, ref) => {
    return (
      <input
        ref={ref}
        type={type}
        className={cn(
          'flex h-8 w-full rounded-md border border-[#E4E4E7] bg-white px-3 py-1 text-[13px] text-foreground',
          'placeholder:text-zinc-400',
          'hover:border-[#D4D4D8]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-0',
          'disabled:cursor-not-allowed disabled:opacity-50',
          'file:border-0 file:bg-transparent file:text-sm file:font-medium',
          className,
        )}
        {...props}
      />
    );
  },
);
Input.displayName = 'Input';
