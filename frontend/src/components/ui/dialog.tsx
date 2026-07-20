/**
 * 轻量弹窗基础组件（tailwind overlay + role="dialog"）。
 *
 * 硬约束：不引入 headlessui / radix / react-modal 等库，走原生 `div role="dialog"`
 * 加 tailwind 类实现——UI 简洁足够 MVP，behavior 上做两件事：
 *   1. ESC 关闭：`useEffect` 绑 window keydown。
 *   2. 点击 backdrop 关闭：点击父 backdrop 触发 onClose；点击卡片内容 stopPropagation。
 *
 * 不用 `<dialog>` HTML 元素：老浏览器兼容差 + 样式覆盖麻烦（form method=dialog、
 * ::backdrop、原生 close 事件），得不偿失。
 *
 * `open=false` 时组件返回 null，DOM 里彻底不渲染——测试可以直接靠 queryByRole 判断。
 */

import { ReactNode, useEffect, useId, useRef } from 'react';

import { cn } from '@/lib/utils';

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** 自定义卡片最大宽度，默认 max-w-2xl。 */
  maxWidthClass?: string;
}

export function Dialog({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  maxWidthClass = 'max-w-2xl',
}: DialogProps) {
  const titleId = useId();
  const cardRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      onMouseDown={(event) => {
        // 只在直接点击 backdrop（不是从卡片里拖出来释放）时关闭：判断 target === currentTarget。
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={cn(
          'relative w-full rounded-lg border border-zinc-200 bg-white shadow-xl',
          'max-h-[90vh] overflow-y-auto',
          maxWidthClass,
        )}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="border-b border-zinc-100 px-5 py-3">
          <div id={titleId} className="text-[15px] font-semibold text-zinc-900">
            {title}
          </div>
          {subtitle ? (
            <div className="mt-0.5 text-[12px] text-zinc-500">{subtitle}</div>
          ) : null}
        </div>
        <div className="px-5 py-4 space-y-4">{children}</div>
        {footer ? (
          <div className="flex items-center justify-end gap-2 border-t border-zinc-100 px-5 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  );
}
