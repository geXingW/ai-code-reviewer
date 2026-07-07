# 前端设计规范 v1（Linear 风格）

本文档定义 AI Code Reviewer 前端 UI 的**唯一设计事实源**。任何 UI 改动必须遵循本规范，
或提交更新本文档的 PR。

参考实现：`docs/design/linear-mockup.html`（Tailwind CDN 静态原型）
参考截图：`docs/design/linear-mockup-viewport.png`（首屏）、`linear-mockup-full.png`（完整长图）

## 设计风格

**Linear 风格** — 极简 · 冷峻 · 高密度 · 克制。

- 灰阶为主视觉，Indigo 仅作激活/链接点缀
- 主 CTA 用黑色 `#18181B`（非 Indigo）
- 边框极淡（`#E4E4E7`），分层靠留白 + 字重而非阴影/粗线
- 中文正文 + JetBrains Mono 呈现所有代码化数据（URL / SHA / 版本号 / MR 编号）

## Design Tokens

### 颜色

| 用途 | Token | Hex |
|------|-------|-----|
| 主区背景 | `bg` | `#FAFAFA` |
| 卡片/表面 | `surface` | `#FFFFFF` |
| 弱分区 | `muted` | `#F4F4F5` |
| 主边框 | `border` | `#E4E4E7` |
| 弱分隔线 | `border-subtle` | `#F4F4F5` |
| 主文本 | `fg` | `#18181B` |
| 次文本 | `fg-muted` | `#52525B` |
| 弱文本 | `fg-subtle` | `#71717A` |
| 辅助/占位 | `fg-faint` | `#A1A1AA` |
| 品牌色（Indigo） | `brand` | `#4F46E5` |
| 品牌浅底 | `brand-subtle` | `#EEF2FF` |
| 主 CTA（黑） | `cta` | `#18181B` |
| CTA hover | `cta-hover` | `#27272A` |
| 成功 | `success` | `#10B981` |
| 危险 | `danger` | `#EF4444` |
| 警告 | `warning` | `#F59E0B` |

### 字体

- **Sans**：Inter（已装，`@fontsource/inter`），启用 `cv11 ss01 ss03`
- **Mono**：JetBrains Mono（已装，`@fontsource/jetbrains-mono`）
- **中文**：系统栈 fallback，无需单独字体

### 字号阶梯

| 用途 | Size | Weight |
|------|------|--------|
| KPI 大数 | 24px | 600 |
| 页面标题 | 18px | 600 |
| 副标题/次要标题 | 15px | 600 |
| 卡片标题 | 13px | 600 |
| 正文 | 13px | 400 |
| Label | 12px | 500 |
| 次要说明 | 12px | 400 |
| 辅助/元信息 | 11px | 400 |
| Section label（大写字距） | 11px | 500 · uppercase · tracking 0.06em |

### 尺寸

- 侧栏宽 `224px`（w-56）
- 顶栏高 `44px`
- 页头 padding `20px 24px 16px 24px`
- 内容区 padding `24px`（p-6）
- 卡片 padding `16px`
- 卡片 header padding `12px 16px`
- 输入框 `field` 高 `32px`，字号 `13px`
- 按钮 主/次均高 `30px`，字号 `13px`
- Nav item 高 `28px`，字号 `13px`
- Badge 高 `20px`，字号 `11px`

### 圆角

- 输入框/按钮/nav item `6px`（rounded-md）
- Badge/kbd `4px`
- 图标按钮 `5px`
- 卡片 `8px`（rounded-lg）
- Logo/头像方 `6px`

### 焦点态

- Focus ring：`box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15)` + `border-color: #6366F1`
- 无粗蓝色 outline

## 骨架结构

```
┌─────────────┬──────────────────────────────────────┐
│ Sidebar     │ Topbar (44px)                        │
│ 224px       │ · breadcrumb · search · notif       │
│             ├──────────────────────────────────────┤
│ · Workspace │ Page Header                          │
│ · Nav 组 1  │ · title 18/600 · desc 13/400        │
│   (工作台)  │ · [refresh] [primary CTA]           │
│ · Nav 组 2  ├──────────────────────────────────────┤
│   (配置)    │ Content area (p-6, space-y-6)       │
│ · User      │  · KPI row (4 cards)                │
│             │  · main sections (card + card)      │
└─────────────┴──────────────────────────────────────┘
```

**导航分组**（侧栏）：
- **工作台**：仪表盘、审查记录、误报队列、问题与误报
- **配置**：模型供应商、审查规则、GitLab 项目、引擎配置

## 组件规范

### Button（shadcn 覆盖）

- `variant="default"`：黑底白字（`#18181B` / hover `#27272A`），高 30px
- `variant="secondary"`：白底 + 1px 边框，高 30px
- `variant="ghost"`：无边框透明底，用于图标按钮
- `variant="link"`：Indigo `#4F46E5`
- 全站禁用 shadcn 默认的 primary Indigo 样式

### Card

- 白底 · 1px `#E4E4E7` 边框 · rounded-lg
- 无阴影
- Header：底部 1px `#F4F4F5` 弱分隔线
- Body 之间使用 `border-b border-zinc-100` 做行分隔（不用完整分割线）

### Input / Textarea / Select

- 统一 32px 高、13px 字号、6px 圆角、`#E4E4E7` 边框
- hover: `#D4D4D8`
- focus: Indigo ring

### Badge

- `success` / `error` / `warning` / `neutral` / `indigo`（默认标识）
- 均为浅底 + 深字 + 同色浅边框（三色组合）
- 状态点用 6px 圆点 `dot` 前置

### 状态点（dot）

- 6×6px 圆点
- emerald / rose / amber 三色

## 页面模式

### KPI 卡片

- 4 张一行，每张 padding 16px
- 结构：`标签 + 图标 // 大数 + 趋势 delta`
- 趋势 delta 用 `text-emerald-600`（正向）/ `text-rose-600`（负向），字号 12px

### 列表项（MR / Provider）

- 每行 padding 12–16px
- 底部 1px `#F4F4F5` 分隔线
- hover: `#FAFAFA` 背景
- 状态圆点 + 主内容 + 元信息（mono 灰色）+ 右侧 badge / action

### 表单

- Label 12/500 灰色，字段下方 4px 间距
- 字段之间垂直间距 12px（space-y-3）
- 按钮组底部右对齐，与表单主体隔 1px 顶部分隔线

## 反模式（禁止）

1. **深色渐变横幅 hero**
2. **纯色按钮排作为一级导航**
3. **Indigo 用于大面积背景/文字**
4. **卡片使用阴影 `shadow-md` 或更深**
5. **绿色 pill 用于版本号、数字等非状态语义**
6. **中文正文使用 monospace 字体**
7. **按钮/输入框超过 32px 高**
8. **多层圆角混用（除 mockup 定义的 4/5/6/8 四档）**

## 版本

- v1（本文档）：Linear 风格首版，随 PR-D 落地
