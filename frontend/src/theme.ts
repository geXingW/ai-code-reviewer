import type { ThemeConfig } from 'antd';

/**
 * antd 主题 token：沿用既有品牌视觉（Indigo #4F46E5 点缀、Linear 灰阶
 * 表面、8px 圆角、Inter 字体）。深浅模式目前仅浅色，与 globals.css 的
 * :root tokens 对齐。
 */
export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: '#4F46E5',
    colorInfo: '#4F46E5',
    colorLink: '#4F46E5',
    colorSuccess: '#10B981',
    colorWarning: '#F59E0B',
    colorError: '#EF4444',
    borderRadius: 8,
    // 灰阶表面：与 globals.css 的 --background/--foreground 一致
    colorBgLayout: '#FAFAFA',
    colorBgContainer: '#FFFFFF',
    colorBorder: '#E4E4E7',
    colorBorderSecondary: '#F4F4F5',
    colorText: '#18181B',
    colorTextSecondary: '#52525B',
    colorTextTertiary: '#71717A',
    colorTextQuaternary: '#A1A1AA',
    fontFamily:
      '"Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
    fontSize: 13,
  },
  components: {
    Layout: {
      siderBg: '#FFFFFF',
      headerBg: '#FFFFFF',
      headerHeight: 48,
      headerPadding: '0 16px',
      bodyBg: '#FAFAFA',
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: 'rgba(0, 0, 0, 0.06)',
      itemSelectedColor: '#18181B',
      itemColor: '#52525B',
      itemHoverColor: '#18181B',
      itemHeight: 30,
      itemMarginInline: 8,
      itemBorderRadius: 6,
      groupTitleFontSize: 11,
      groupTitleColor: '#A1A1AA',
    },
    Table: {
      headerBg: '#FAFAFA',
      headerColor: '#71717A',
      cellPaddingBlock: 10,
      fontSize: 13,
    },
    Card: {
      paddingLG: 16,
    },
    Modal: {
      titleFontSize: 15,
    },
  },
};
