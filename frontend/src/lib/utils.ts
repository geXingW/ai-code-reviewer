import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * `cn` 合并 className 的标准 shadcn 惯例：
 * - 先用 `clsx` 处理条件 / 数组 / 对象
 * - 再用 `tailwind-merge` 合并冲突的 Tailwind 类
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
