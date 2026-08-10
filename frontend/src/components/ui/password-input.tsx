import * as React from 'react';
import { Eye, EyeOff } from 'lucide-react';

import { cn } from '@/lib/utils';
import { Input, type InputProps } from './input';

/**
 * PasswordInput：在 Input 基础上叠加显示/隐藏切换按钮。
 *
 * - 默认 type="password"（掩码显示）
 * - 点击右侧眼睛图标切换为 type="text"（明文显示）
 * - 切换按钮 aria-label 语义化，便于屏幕阅读器与测试定位
 *
 * 其余 props 透传给底层 Input，保持一致的边框 / focus 样式。
 */
export interface PasswordInputProps extends Omit<InputProps, 'type'> {
  /** 自定义明文切换按钮的 aria-label。 */
  toggleAriaLabel?: string;
}

export const PasswordInput = React.forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ className, toggleAriaLabel, ...props }, ref) => {
    const [visible, setVisible] = React.useState(false);

    return (
      <div className="relative">
        <Input
          ref={ref}
          type={visible ? 'text' : 'password'}
          className={cn('pr-9', className)}
          {...props}
        />
        <button
          type="button"
          aria-label={toggleAriaLabel ?? '切换密码显示'}
          aria-pressed={visible}
          onClick={() => setVisible((prev) => !prev)}
          className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center justify-center rounded p-1 text-zinc-400 transition-colors hover:text-zinc-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      </div>
    );
  },
);
PasswordInput.displayName = 'PasswordInput';
