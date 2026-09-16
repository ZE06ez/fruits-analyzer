# UI Architecture

更新时间：2026-09-10

本文档记录主检测软件的信息架构约束。当前主程序仍是 `host_software/static_ui_prototype_bin/` 下的静态 HTML/CSS/JS + Python 本地 HTTP 后端；Model Studio 继续作为独立工作空间通过 `/model-studio` 打开，不合并回主检测页面。

## 1. Workspace Boundaries

主检测软件分为五个一级工作区：

- 检测工作台：普通检测人员的默认入口，围绕样品、设备就绪、采集、分析和结果。
- 样品与记录：查看本次或本地样品数据、图片、数据完整性和报告入口。
- 设备与维护：工程调试入口，包括 STM32、设备发现/绑定、相机维护、光源、滤光轮、推杆和样品台边界。
- 模型训练：只作为在当前浏览器页打开 `/model-studio` 的入口；Dataset、Training、Models 仍在独立 Model Studio 内完成，返回检测工作站也必须在同一页完成，避免额外生成检测中心窗口。
- 系统设置：软件级设置和预留配置，不重复承载硬件手动控制。

## 2. Operator Workflow

普通检测工作台按以下顺序组织：

```text
新建样品 -> 设备准备 -> 数据采集 -> 分析详情 -> 检测结果
```

默认屏幕必须让用户快速看到：

- 当前样品。
- 系统是否就绪。
- 当前流程步骤。
- 下一步主要操作。

每个页面最多保留一个最明显 Primary Action。若操作被禁用，周围状态或提示必须说明原因，不能只依赖灰色按钮让用户猜测。

检测工作台左侧只保留“样品与采集 / 分析详情 / 检测结果”三项。数据预处理、ROI 与特征、糖度预测、酸度与 pH、糖酸比是“分析详情”内部步骤，不再作为整个软件的重复一级入口。

样品与采集页按“当前任务 -> 必要状态 -> 主要操作 -> 次要设置 -> 高级/技术详情”组织。正常检测和训练数据采集必须明显分流：正常检测显示检测模型状态；训练数据采集只采图像和 metadata，不显示检测模型区块，也不因为没有 Production/Default 模型而阻塞新建样品。

采集设置通过低视觉权重入口打开，承载 Dark/White Calibration 摘要和 Background Reference 管理。Background Reference 是 RGB 前景分割参考图，不得与 Dark Reference、White Reference、CalibrationSet 或 calibrationId 共用语义。

## 3. Engineer Workflow

工程入口集中在“设备与维护”。普通检测流程不要求用户理解以下字段：

- COM、device index、VID/PID、stable ID。
- DVP2 handle、AA55 protocol、raw diagnostics。
- 滤光轮手动运动、推杆动作、样品台调试。

这些能力必须通过 Progressive Disclosure 暴露，例如详情面板、相机维护页或设备维护页。工程调试按钮不得被包装成普通检测流程的必需步骤。

## 4. Camera Preview Placement

相机预览只在需要采图或维护相机时显示：

- 检测工作台 / 数据采集。
- 相机检查。
- 相机维护。

智能分析、结果、系统设置和非相机设备维护页面默认不显示相机预览，以减少普通流程中的视觉噪声。

## 5. Scientific Truthfulness

UI 信息架构不得改变底层真实性边界：

- Preview JPEG 只代表浏览器预览，不代表 scientific capture。
- Offline/Demo 数据不得标记为真实采集。
- 没有 Production/Default 模型时显示缺失状态，不伪造 SSC/TA/pH 数值。
- Sample rotation 与 filter wheel rotation 必须保持独立。
- 未接入或未验收硬件必须显示 unknown / not ready / unsupported / blocked。
