import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

/**
 * Button 组件（Linear 风格覆盖）
 *
 * variant：视觉基调
 *   - default    → 主 CTA 黑底白字（#18181B / hover #27272A）
 *   - secondary  → 白底 + 1px 边框
 *   - outline    → 描边
 *   - ghost      → 透明底 hover 变浅灰
 *   - destructive→ 危险操作
 *   - link       → Indigo 文本链接
 * size：尺寸（高 32px 为默认；sm/lg/icon 分别 28/36/32）
 */
export const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-[13px] font-medium ' +
    'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ' +
    'disabled:pointer-events-none disabled:opacity-50 ' +
    '[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-zinc-800',
        secondary: 'bg-white text-foreground border border-input hover:bg-[#FAFAFA]',
        outline:
          'border border-input bg-background hover:bg-accent hover:text-accent-foreground',
        ghost:
          'hover:bg-accent hover:text-accent-foreground',
        destructive:
          'bg-destructive text-destructive-foreground hover:bg-destructive/90',
        link: 'text-brand underline-offset-4 hover:underline',
      },
      size: {
        sm: 'h-7 rounded-md px-3 text-xs',
        default: 'h-8 rounded-md px-3',
        lg: 'h-9 rounded-md px-4',
        icon: 'size-8',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, type = 'button', ...props }, ref) => {
    return (
      <button
        ref={ref}
        type={type}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  },
);
Button.displayName = 'Button';
