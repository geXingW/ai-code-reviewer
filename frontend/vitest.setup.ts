import '@testing-library/jest-dom/vitest';

// jsdom 未实现 matchMedia，antd 的 Drawer/Timeline 等组件挂载时会读取
// window.matchMedia（响应式断点），缺失会直接抛 TypeError 导致组件树崩溃。
if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
