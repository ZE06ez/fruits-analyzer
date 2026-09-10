# UI Design System

更新时间：2026-09-10

本文档记录主检测软件的视觉设计契约。目标是工业科研仪器工作站，而不是 SaaS Dashboard、科技大屏或营销页。

## 1. Palette

必须保留当前品牌色：

- Background：`--bg-0 #0f172a`、`--bg-1 #172033`、`--bg-2 #1e293b`
- Surface：`--panel`、`--panel-strong`
- Text：`--text #e2e8f0`、`--muted #94a3b8`、`--soft #cbd5e1`
- Accent：`--purple #a78bfa`、`--fuchsia #e879f9`、`--blue #67e8f9`
- Semantic：`--green #86efac`、`--amber #fde047`、`--red #f87171`

允许调整 alpha、border、shadow、surface 层级和 hover 强度；禁止无理由改成全新主题。

## 2. Surface

最多使用四层：

- Surface 0：页面背景。
- Surface 1：主工作区。
- Surface 2：Panel / Card。
- Surface 3：Modal / Active Area。

普通内部区域优先使用背景、1px border 和 spacing 表达层级。只有顶层工作区、浮层和重要区域使用 shadow。

## 3. Geometry

- Radius：`6px / 8px / 10px`。
- Pill：仅用于 status chip、badge。
- Spacing：`4 / 8 / 12 / 16 / 20 / 24 / 32`。
- Control height：Compact `32px`、Default `36px`、Primary `42px`。

## 4. Buttons

按钮类型：

- Primary：当前页面唯一主要操作，紫/青方向，带细边框、顶部 highlight 和克制 shadow。
- Secondary：深色 slate surface，边框清晰，权重低于 Primary。
- Ghost：刷新、查看、取消等辅助动作，默认低视觉重量。
- Danger：Emergency Stop、Permanent Delete、危险 reset，不用于普通重要操作。
- Icon：小型工具按钮，使用统一 outline 风格。

所有按钮必须具备 normal、hover、active、focus-visible、disabled 和 loading/aria-busy 可识别状态。Disabled 不只依赖 opacity，需要降低背景、边框、文字和 shadow。

## 5. Inputs

输入框保持深色背景。Normal、hover、focus、readonly、disabled 必须可区分；focus 使用 Purple/Cyan ring，避免强 neon glow。

## 6. Status

状态必须使用 dot/icon + text，不只靠颜色：

- Ready / Success：green。
- Running：cyan，可有极轻微 active 感。
- Waiting / Offline：muted gray。
- Warning / Manual Required：amber。
- Error / Failed：red。

## 7. Typography

字体保持 `Segoe UI`, `Microsoft YaHei`, Arial。减少 800/900 字重滥用：

- Page Title：600-700。
- Section / Panel Title：600。
- Body：400-500。
- Label：500-600。
- Metadata：400-500。
- 关键数值：最多 700。

## 8. Motion

动画保持 120-180ms，只用于 hover、pressed、panel transition 和进度反馈。禁止弹跳、大幅缩放、长距离 slide 和无意义的频繁 fade。必须尊重 `prefers-reduced-motion`。

