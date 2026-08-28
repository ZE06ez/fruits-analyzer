# Changelog

本文档只记录能从 Git 历史或当前代码确认的阶段。无法确认具体日期的内容标记为“历史版本，具体日期待确认”。

## 2026-08-27 主程序采集/分析中央布局切换

- 修改内容：主程序中央工作区新增采集模式和分析模式布局；设备准备、采集、设置等模块保留 RGB/多光谱相机预览，形态、糖度、酸度和口感分析模块隐藏相机预览并让分析内容重排占用中央空间。
- 修改文件：
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/styles.css`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
- 为什么修改：区分“通过设备采集样品”和“分析已有/本次样品数据”两种使用场景，避免分析页面被两个大相机面板挤压。
- 是否影响原有功能：不改后端 API、采集流程、形态分析、预测、训练或 Model Studio；只根据当前模块 key 切换前端布局 class，不清空设备、样品或模型状态。

## 2026-08-24 Model Studio 数据准备流程 UI 重组

- 修改内容：将 Dataset 页面从按钮平铺改为 Dataset Preparation workflow，包含创建数据集、导入样品、标签录入、数据质量、创建版本五步；将 Dataset Summary、Readiness 和 Quality 状态移到右侧检查栏；将“样品与标签”页面重组为样品列表、选中样品 Ground Truth、批量 labels.csv 和 Sample Quality 区域；刷新/检查/选择文件夹改为辅助或 Ghost 操作。
- 修改文件：
  - `host_software/static_ui_prototype_bin/model_studio/static/index.html`
  - `host_software/static_ui_prototype_bin/model_studio/static/model_studio.css`
  - `host_software/static_ui_prototype_bin/model_studio/static/model_studio.js`
  - `docs/CHANGELOG.md`
- 为什么修改：减少 Dataset 和样品标签页面的按钮同质化，让用户按“Dataset -> Samples -> Labels -> Quality -> Version -> Training”的依赖顺序自然完成数据准备，降低创建空 Dataset Version 后训练失败的概率。
- 是否影响原有功能：不改业务 API、数据库结构、Dataset Version 语义、训练算法、模型发布逻辑；属于前端 UI/UX 增量优化。

## 2026-08-24 Model Studio 空数据集训练错误处理

- 修改内容：开始训练前校验 Dataset Version 是否包含样品和当前目标标签；空版本或无目标标签时直接返回明确错误，不再启动必然失败的训练任务；失败任务进度显示为终止状态，避免看起来像进度条卡住。
- 修改文件：
  - `host_software/static_ui_prototype_bin/model_studio/service.py`
  - `host_software/static_ui_prototype_bin/model_studio/static/model_studio.js`
  - `host_software/static_ui_prototype_bin/tests/test_model_studio_service.py`
  - `docs/CHANGELOG.md`
- 为什么修改：用户在 `Dataset V1 · 0 samples` 上启动训练时会得到 `Insufficient training dataset`，旧进度条停在失败前进度，容易误解为训练仍在运行。
- 是否影响原有功能：不影响正常训练；只阻止没有样品或没有目标标签的训练任务创建。测试已覆盖空 Dataset Version 不能启动训练。

## 2026-08-23 本地形态分析与采集前置条件调整

- 修改内容：本地已有样品目录可直接进行形态分析，不再要求先创建当前样品；样品采集链路新增设备准备前置条件，连接检查、电机、光源、相机和标定检查全部完成后才能创建样品/完成采集；RGB/多光谱目录名输入框不再被后端绝对路径覆盖。
- 修改文件：
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_data_flow.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
- 为什么修改：区分“分析本地已有样品目录”和“通过设备采集新样品”两条流程，避免用户导入有效目录后仍因未创建样品而无法形态分析，同时保证采集流程必须先经过设备准备。
- 是否影响原有功能：影响主工作站采集入口；当前设备准备仍是离线模拟状态，不代表真实硬件已接入。测试已覆盖无当前样品的本地形态分析和采集前置条件。

## 2026-08-22 统一系统路径选择器

- 修改内容：新增通用 `/api/select-folder` 和 `/api/select-file`；主程序保存位置/其他样品文件夹、Model Studio 默认导入来源/样品文件夹/labels.csv 改为只读路径显示 + 系统选择按钮；取消选择时保留原路径；选择后返回路径校验状态。
- 修改文件：
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/index.html`
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/model_studio/static/index.html`
  - `host_software/static_ui_prototype_bin/model_studio/static/model_studio.js`
  - `host_software/static_ui_prototype_bin/model_studio/static/model_studio.css`
  - `host_software/static_ui_prototype_bin/tests/test_backend_data_flow.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `AGENTS.md`
- 为什么修改：减少普通用户手动输入 Windows 路径的错误，并统一主程序与 Model Studio 的路径选择体验。
- 是否影响原有功能：不改变样品创建、导入、训练、预测和发布业务语义；旧 `/api/select-dataset`、`/api/select-save-root` 仍保留兼容。

## 2026-08-22 Model Studio 本地托管导入与标签显式保存

- 修改内容：Dataset 创建本地托管目录；样品导入从外部 Sample Folder 复制到 `model_studio_data/datasets/<dataset_id>/samples/`；新增导入验证、重复样品处理、单样品 SSC/TA/pH 保存、`labels.csv` 同步、Sample 删除记录/本地副本、Dataset dirty 标记和 Dataset Version 样品/标签快照。
- 修改文件：
  - `host_software/static_ui_prototype_bin/model_studio/service.py`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/model_studio/static/index.html`
  - `host_software/static_ui_prototype_bin/model_studio/static/model_studio.js`
  - `host_software/static_ui_prototype_bin/model_studio/static/model_studio.css`
  - `host_software/static_ui_prototype_bin/tests/test_model_studio_service.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
- 为什么修改：避免训练依赖用户电脑任意外部目录，并让实验标签成为明确保存、可迁移、可复现的数据资产。
- 是否影响原有功能：影响 Model Studio 数据导入路径；旧的 `storagePath` 仍可作为默认导入来源，训练改为读取本地托管副本。现有测试通过。

## 2026-08-21 项目上下文系统建立

- 修改内容：新增项目长期上下文文档。
- 修改文件：
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `AGENTS.md`
- 为什么修改：项目经过多轮 Codex 对话后，单次聊天上下文不足，需要用仓库内文档固化真实状态、需求、架构和后续协作规则。
- 是否影响原有功能：不影响业务代码和 UI。

## 历史版本，具体日期待确认：添加水果分析桌面源码

Git commit：`eb79035 Add fruit analyzer desktop source`

- 修改内容：引入上位机原型源码。
- 影响范围：当前 `host_software/static_ui_prototype_bin` 的基础来源。
- 备注：本次整理未展开该提交的全部细节。

## 历史版本，具体日期待确认：适配双相机工作流

Git commit：`ffd8955 Adapt analyzer for two-camera workflow`

- 修改内容：将旧路径整理到 `host_software/static_ui_prototype_bin`，补充主 UI、后端、形态分析和样品数据说明；移除旧内置示例图片资产。
- 修改文件：`app.js`、`backend_server.py`、`index.html`、`pointcloud_service.py`、`styles.css`、`sample_data/README.md`、`run_analyzer.bat` 等。
- 为什么修改：从旧 RGB-D/点云演示逐步转向 RGB + 多光谱两相机工作流。
- 是否影响原有功能：改变样品数据组织和形态分析入口；旧点云路径作为兼容存在。

## 历史版本，具体日期待确认：加入多光谱品质训练框架

Git commit：`889763c Add multispectral quality training framework`

- 修改内容：新增 `quality_algorithm/`、`training/`、`quality_prediction.py`，支持多光谱样品目录检查、暗/白校正、ROI 特征、RAW/SNV/MSC、PLSR/SVR/RF 训练和预测结果结构。
- 修改文件：`quality_algorithm/*`、`training/*`、`quality_prediction.py`、相关 UI/API 和测试。
- 为什么修改：让 SSC/TA/pH 从前端占位走向可训练、可保存、可加载的真实模型接口。
- 是否影响原有功能：预测入口不再伪造值；无模型时返回 `model_missing`。

## 历史版本，具体日期待确认：加入样品会话和模型选择流程

Git commit：`23dc794 Add sample session model selection flow`

- 修改内容：新增 Model Studio，支持数据集、样品、标签、特征、训练实验、候选模型、发布/默认模型；主程序新增果种/品种/目标模型选择。
- 修改文件：`model_studio/service.py`、`model_studio/static/*`、`backend_server.py`、`app.js`、`quality_prediction.py`、`tests/test_model_studio_service.py` 等。
- 为什么修改：把离线训练与主检测工作站连接起来，并保证 Production 模型由人工发布。
- 是否影响原有功能：主程序预测会按样品 fruit_type/variety 和已选模型解析。

## 历史版本，具体日期待确认：优化样品会话和分析模型选择

Git commit：`529ebaf Refine sample session and analysis model selection`

- 修改内容：优化主 UI 的样品创建、当前/其他目录分析、模型选择、样品状态和样式；后端增强当前样品会话与模型选择验证。
- 修改文件：`app.js`、`backend_server.py`、`index.html`、`styles.css`、`model_studio/static/model_studio.css`、`tests/test_backend_data_flow.py`。
- 为什么修改：让当前拍摄目录、手动分析目录、果种/品种模型选择之间的状态更一致。
- 是否影响原有功能：影响主工作站数据流；测试覆盖模型作用域和当前/手动目录切换。
