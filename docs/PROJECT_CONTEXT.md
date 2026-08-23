# Project Context

更新时间：2026-08-22

本文档记录当前项目的真实上下文。判断优先级固定为：当前真实代码 > 当前配置/数据库结构 > 当前测试 > 最新项目文档 > 历史项目文档 > 历史聊天上下文。若历史描述与代码冲突，以代码为准。

## 1. 项目目标

本项目要做一套果实口感与品质多光谱无损检测系统。目标设备把水果样品放入封闭暗箱，通过稳定光源、RGB 彩色相机、黑白相机和滤光片转轮采集图像，再由 Python 上位机完成样品管理、数据保存、图像/光谱特征提取、模型预测和结果展示。

当前重点指标：

- SSC：可溶性固形物/糖度，单位 `°Brix`。
- TA：可滴定酸，当前预测结果单位显示为 `%`。
- pH：酸碱度。
- 糖酸比与口感等级：由 SSC 和 TA 计算并在 UI 展示。
- 形态与表面：当前主要基于 RGB 图像测量面积、水平宽度、垂直高度、颜色均匀度、果粉覆盖率；兼容旧 RGB-D/PLY 点云流程，但三维建模在主 UI 中标为后续预留。

系统最终要解决的问题是：用可重复、可追溯、尽量无损的方式替代人工目测或破坏性理化检测的一部分流程，使水果样品从采集、建模、预测到报告输出形成闭环。

## 2. 硬件架构

目标硬件关系：

```text
封闭暗箱
  -> 样品台/可能的旋转或升降机构
  -> RGB 彩色相机：采集外观、颜色、纹理、形态
  -> 黑白相机：配合滤光片转轮采集多光谱灰度图
  -> LED/卤钨/近红外等光源：提供稳定照明
  -> 滤光片转轮：按孔位切换中心波长
  -> 电机/驱动器/STM32：控制滤光轮、光源、门控/样品台等
  -> Python 上位机：控制流程、保存数据、运行算法和 UI
```

硬件状态表：

| 模块 | 目标/文档描述 | 当前代码状态 |
| --- | --- | --- |
| RGB 彩色相机 | 文档建议 RER-USB48MP01 等工业 USB 彩色相机，用于外观和形态 | MOCK/PARTIAL：UI 有彩色相机预览区；采集由 `create_offline_capture_dataset()` 写入模拟 PNG；没有相机 SDK 或真实采集代码 |
| 黑白多光谱相机 | 文档建议 MGS231M-H2 黑白相机，400-1000 nm，配合滤光片 | MOCK/PARTIAL：数据目录和算法支持 `multispectral/` 灰度图；没有真实相机 SDK |
| 滤光片转轮 | 文档建议 8-16 孔，STM32 控制 HOME/定位；代码开发配置启用 450/560/670 nm | PARTIAL/MOCK：`quality_algorithm/filter_config.development.json` 有软件配置；没有串口协议和电机控制实现 |
| 光源 | 顶部/侧向/底部/紫外/近红外光源，可调亮度并与曝光同步 | MOCK：UI 有光源波段按钮和自检日志；没有真实 LED/调光通信 |
| 电机/驱动器 | STM32 + 步进驱动器控制滤光轮、可能的样品台/门控 | MOCK：UI 有电机自检、平台正反转按钮；点击只写日志 |
| 暗箱/样品台 | 封闭光环境，固定样品位置 | TODO：代码只体现需求和文档，没有传感器/门控/限位状态接入 |
| 真实设备状态读取 | 门状态、急停、电机报警、温度等 | TODO：`/api/status` 返回后端依赖和会话状态，不返回真实硬件状态 |

## 3. 软件架构

当前真实软件不是 Vue/FastAPI/Electron，而是：

- 前端：静态 `index.html` + `styles.css` + `app.js`。
- 后端：`backend_server.py` 使用 Python 标准库 `ThreadingHTTPServer` 提供静态资源和 JSON API。
- 桌面入口：`launcher.py` 启动本地后端并打开浏览器；`FruitTasteAnalyzer.spec` 用 PyInstaller 打包。
- 模型训练中心：`model_studio/`，同一个后端下的 `/model-studio` 静态页面和 `/api/model-studio/*` API。
- 算法：`pointcloud_service.py`、`pipeline_v2.py`、`quality_algorithm/`、`training/`、`quality_prediction.py`。

主数据流：

```text
UI
  -> backend_server.py / SessionState / JobStore
  -> 样品目录 Data/<timestamp>_<sample_name> 或用户选择目录
  -> rgb/ + multispectral/ + calibration/dark + calibration/white + metadata.json
  -> pointcloud_service.py 形态/表面分析
  -> quality_algorithm.spectral_features 提取多光谱特征
  -> quality_prediction.predict_ssc/predict_ta/predict_ph
  -> PredictionResult
  -> app.js 更新结果栏、糖酸比和报告文本
```

关键文件：

- `host_software/static_ui_prototype_bin/index.html`：主检测工作站 UI。
- `host_software/static_ui_prototype_bin/app.js`：前端状态机、样品流程、模型选择、分析调用、结果渲染。
- `host_software/static_ui_prototype_bin/backend_server.py`：HTTP API、样品会话、目录选择、离线采集、形态任务、预测接口、Model Studio API 转发。
- `host_software/static_ui_prototype_bin/pointcloud_service.py`：样品目录检查、RGB/多光谱二维形态与表面分析、兼容 RGB-D/PLY。
- `host_software/static_ui_prototype_bin/pipeline_v2.py`：旧 RGB-D/SFM 点云重建工具函数。
- `host_software/static_ui_prototype_bin/quality_prediction.py`：`SampleSession`、`PredictionResult`、SSC/TA/pH 预测入口。
- `host_software/static_ui_prototype_bin/quality_algorithm/`：滤光片配置、暗/白校正、ROI、特征提取、预处理、模型 IO。
- `host_software/static_ui_prototype_bin/training/`：特征 CSV 构建、PLSR/SVR/RF 训练、评估。
- `host_software/static_ui_prototype_bin/model_studio/service.py`：SQLite 数据集、样品、标签、训练实验、候选模型、发布模型管理。

## 4. 用户完整工作流（当前代码）

当前主工作流以离线/本地文件夹为主：

1. 启动软件：运行 `python launcher.py` 或打包后的 EXE。
2. `launcher.py` 启动本地后端并打开浏览器页面。
3. UI 加载 `/api/status`，获取依赖状态、默认保存根目录、当前样品会话、Model Studio 发布模型目录。
4. 用户进行设备准备页面的连接检查、电机自检、光源自检、相机自检和标定检查：当前仍属于离线模拟，但前端和后端都会把它作为样品采集前置条件。
5. 用户在“样品采集”中填写样品名称、样品种类、品种；保存位置通过系统“选择文件夹”按钮写入只读路径框，取消选择不会清空旧路径。
6. 完成设备准备后点击“新建样品”：`POST /api/new-sample` 创建唯一样品目录，写入 `metadata.json`，并创建 `rgb/`、`multispectral/`、`calibration/dark/`、`calibration/white/`。
7. 用户在 SSC/TA/pH 页面选择已发布且兼容果种/品种/指标的模型；也可自动使用默认模型。
8. 用户点击采集步骤：当前只更新 UI 进度与日志。
9. 点击“进入分析”或完成采集：`POST /api/complete-capture` 调用 `create_offline_capture_dataset()`，向当前样品目录写入模拟 RGB、多光谱、暗场、白板图片，并把本次目录设为 `analysisDataDir`。
10. 形态分析页可选择“本次拍摄”或“其他文件夹”；“其他文件夹”通过系统目录选择器选择，`GET /api/sample-folder` 检查目录结构，支持 `rgb/` 和 `multispectral/`。本地已有样品目录可以直接分析，不强制先创建当前样品。
11. `GET /api/dataset-images` 返回图片预览 URL；前端显示彩色图和多光谱图。
12. 点击“开始形态分析”：`POST /api/analyze-shape` 创建后台任务，`pointcloud_service.analyze_rgbd_dataset()` 优先执行 RGB + multispectral 二维形态/表面分析。
13. 后端生成输出图到 `outputs/<job_id>/`，前端轮询 `/api/jobs/<job_id>` 并显示面积、宽度、高度、果粉覆盖率、颜色均匀度等。
14. 点击“开始糖度分析”：`POST /api/predict-ssc` 构建 `SampleSession`，调用 `predict_ssc()`。
15. 点击“开始酸度分析”：`POST /api/predict-acid` 调用 `predict_ta()` 和 `predict_ph()`。
16. 若存在兼容 Production/Default 模型文件和元数据，预测返回 `success`；否则返回 `model_missing`，不会伪造数值。
17. 点击“生成口感分析”：前端用 SSC / TA 计算糖酸比并给出等级；若缺少有效 SSC 或 TA，会提示等待数据。
18. 导出报告：前端生成本地 TXT 文本，说明硬件仍为预留。

## 5. Model Studio 工作流

当前 Model Studio 已有真实后端和 UI：

1. 从主界面点击“模型训练”，打开 `/model-studio`。
2. 新建 Dataset：填写名称、果种、品种；可选默认导入来源通过系统“选择文件夹”写入只读路径框；`ModelStudioService.create_dataset()` 在 SQLite 登记元数据，并创建本地托管目录 `model_studio_data/datasets/<dataset_id>/samples/` 与 `labels.csv`。
3. 导入样品：用户通过系统“选择文件夹”选择主程序保存的 Sample Folder，后端先用 `validate_sample_folder()`/`inspect_sample_structure()` 检查 RGB、多光谱、暗/白校正和 `metadata.json`，返回 Valid/Warning/Invalid。
4. 确认导入后，`import_samples()` 使用 COPY 把外部 Sample Folder 复制到 Dataset 本地仓库；SQLite `samples.storage_path`/`local_path` 指向本地副本，`source_path` 只记录原始来源。重复样品默认跳过，可作为新样品导入，不静默覆盖。
5. 导入或录入实验标签：仍支持通过系统“选择文件”选择 `labels.csv`，格式为 `sample_id,ssc,ta,ph`；同时样品详情可手动输入 SSC/TA/pH，只有点击“保存标签”才写入 SQLite。
6. 标签保存：`save_sample_label()` 校验数值，允许部分标签，更新 `labels` 表和 `samples.ssc/ta/ph`，同步 Dataset 本地 `labels.csv`，并标记 Dataset `dirty=1`。
7. 数据质量检查：统计缺失波段、缺失校准、缺失标签、坏图、Excluded/Needs Review 样品。
8. 创建数据集版本：对当前 Included 样品列表、本地路径和标签值生成 `sample_snapshot_json`/`label_snapshot_json` 与 hash；后续标签修改不会改变旧 Version 的历史含义。
9. 生成特征：`generate_features()` 按 Dataset Version 快照中的本地 `local_path` 调用 `extract_feature_record()`，输出 `model_studio/artifacts/features/<version>_features.csv`。
10. 创建训练实验：选择单一 target（SSC/TA/pH）、模型组合（PLSR/SVR/RF）、预处理组合（RAW/SNV/MSC）、验证方式（GroupKFold 或 TrainTestSplit）。
11. 启动训练任务：后台线程运行 `_run_training_job()`，对每组预处理/模型组合调用 `training.train.train_one()`。
12. 模型比较：训练结果按 RMSE 排序，模型注册为 `Candidate`，文件在 `model_studio/models/candidates/...`。
13. 人工验证/发布：可标记 Validated，可手动 Publish。
14. 设为默认：发布时选择 `setDefault` 或后续点击“设为默认”；只有此时模型才复制到 `trained_models/<target>/` 作为主程序 fallback。
15. 主程序使用 Production/Default：`quality_prediction._select_registry_model()` 先查 Model Studio SQLite 中 Published/Default/Production 模型并校验果种/品种；没有指定/默认模型时才回落到 `trained_models/<target>`。

## 6. 模型系统

统一预测结果结构在 `quality_prediction.PredictionResult`：

- `value`: 预测值，失败时为 `None`。
- `unit`: SSC 为 `°Brix`，TA 为 `%`，pH 为 `pH`。
- `confidence`: 当前始终为 `None`。
- `model_name`, `model_version`, `model_id`, `model_type`, `preprocessing`。
- `sample_count`: 当前对有效样品按 1 处理。
- `elapsed_time`, `status`, `error_message`。

预测入口：

- `predict_ssc(sample_data)` -> `_predict_target(target="ssc", unit="°Brix")`。
- `predict_ta(sample_data)` -> `_predict_target(target="ta", unit="%")`。
- `predict_ph(sample_data)` -> `_predict_target(target="ph", unit="pH")`。

模型加载规则：

1. 若 `SampleSession` 指定 `selected_ssc_model_id` / `selected_ta_model_id` / `selected_ph_model_id`，则查 `model_studio/database/model_studio.sqlite` 中同 target 且状态为 Published/Default/Production 的模型。
2. 若未指定，按果种/品种查 Default 模型，优先品种精确匹配，再使用 `generic`。
3. 若数据库不存在或无匹配，回落到 `trained_models/<target>/model.joblib` + `metadata.json`。
4. 如果模型文件缺失，返回 `status="model_missing"`。

训练与保存：

- `training/build_dataset.py` 从样品目录和 labels.csv 构建 feature CSV。
- `training/train.py` 支持 `PLSR`、`SVR`、`RF`，支持 `RAW`、`SNV`、`MSC`。
- 保存模型 bundle：`quality_algorithm.model_io.save_model_bundle()` 写 `model.joblib` 与 `metadata.json`。
- `metadata.json` 记录 target、model type、preprocessing、preprocessing_state、wavelengths、feature_names、验证指标、sample_count、calibration_required 等。
- `model_io.validate_feature_record()` 会严格校验波长列表和是否需要校准。

当前真实状态：

- `trained_models/ssc`、`trained_models/ta`、`trained_models/ph` 当前没有提交可用模型文件。
- `model_studio/database/model_studio.sqlite` 当前为空/未初始化数据。
- 预测链路有真实代码和测试，但需要真实训练数据与人工发布模型后才会输出数值。

## 7. 已完成 / 部分完成 / 未完成 / 模拟

| 模块 | 状态 | 依据 |
| --- | --- | --- |
| 主工作站静态 UI | DONE | `index.html`/`styles.css`/`app.js` 完整界面与交互 |
| 本地 Python HTTP 后端 | DONE | `backend_server.py` 提供静态资源、API、任务轮询 |
| PyInstaller 打包配置 | DONE | `FruitTasteAnalyzer.spec`、`launcher.py`、`run_analyzer.bat` |
| 样品创建与目录结构 | DONE | 设备准备完成后，`/api/new-sample` 创建目录和 `metadata.json` |
| 本次拍摄目录进入分析流程 | PARTIAL/MOCK | 会自动设为 `analysisDataDir`，但图片由离线函数生成 |
| 手动选择其他数据目录 | DONE | 主 UI 通过 `/api/select-folder` 系统目录选择器选择其他样品目录，再由 `/api/sample-folder` 检查 |
| 路径选择 UI | DONE | 主程序保存位置/其他样品文件夹、Model Studio 导入来源/样品文件夹/labels.csv 均为只读路径显示 + 系统选择按钮 |
| RGB + 多光谱目录检查 | DONE | `inspect_sample_folder()` 按启用波段检查 |
| RGB 二维形态/表面分析 | DONE/PARTIAL | 可测面积、宽高、颜色、果粉；不是完整真实尺寸标定 |
| RGB-D/PLY 点云兼容 | PARTIAL | 旧流程可用，主 UI 标为三维建模预留 |
| 多光谱特征提取 | DONE | 暗/白校正、ROI 均值、波长校验 |
| RAW/SNV/MSC | DONE | `preprocessing.py` |
| PLSR/SVR/RF 训练 | DONE | `training/train.py` 与测试 |
| Model Studio 数据集/版本/训练/发布 | DONE/PARTIAL | 后端和 UI 已有；Dataset 已本地托管并复制导入样品，实际项目数据库暂无真实训练数据 |
| Production 模型人工发布 | DONE | `publish_model()`/`set_default_model()`；复制到 `trained_models/<target>` |
| 主程序按果种/品种选模型 | DONE | `/api/quality-models`、`resolve_model_id()`、`_select_registry_model()` |
| SSC/TA/pH 预测入口 | DONE/PARTIAL | 真实加载模型预测；当前无生产模型时返回缺失 |
| 糖酸比/口感分析 | PARTIAL | 前端根据预测值计算，等级规则较简单 |
| 真实相机 SDK | TODO | 无 SDK/驱动封装 |
| 真实电机/滤光轮串口 | TODO | 无串口协议实现 |
| 真实光源控制 | TODO | 只有 UI 模拟 |
| 门控/急停/温度/报警 | TODO | 只有历史需求文档 |
| 标定配准 | PARTIAL/TODO | 暗/白校正有；RGB 到多光谱 calibrated registration 未接入 |
| 历史记录数据库/正式报告 | TODO/PARTIAL | Model Studio 有 SQLite；检测结果未持久化到历史库，报告为前端 TXT |

## 8. 关键设计决策

这些决策已经由代码、测试或项目要求体现，后续不要随意推翻：

- 不推倒现有 UI 重做；在当前三栏工作站界面基础上优化。
- 主程序继续以 Python 上位机为核心。
- 当前前端是静态 HTML/CSS/JS，后端是 Python 本地 HTTPServer；历史文档中的 Vue/FastAPI/Electron 是早期建议，不是当前实现。
- 相机 SDK 预计通过厂家 C/C++ SDK 与 Python 对接；当前尚未接入。
- 第一版模型以 PLSR 为基线，SVR 作为主要对照，RF 作为额外比较模型。
- 预处理支持 RAW / SNV / MSC。
- 训练数据不足时必须失败，不能造假标签或伪造模型结果。
- Production/Default 模型必须人工发布；系统不能自动替换正式模型。
- 支持不同水果/品种使用不同模型，并允许 `generic` 品种兜底。
- 本次拍摄目录可以自动进入分析流程。
- 同时允许用户手动选择其他数据目录。
- 文件夹/文件路径选择优先使用系统原生选择器，UI 只显示只读路径；取消选择不得清空旧路径。
- 候选模型必须与 Production/Default 隔离。
- 模型输入波长必须与 metadata 匹配；校准要求必须由模型 metadata 控制。
- 当前 `quality_algorithm/filter_config.development.json` 是开发离线配置，正式训练前必须替换为实测滤光轮配置。

## 9. 当前技术债务

代码和文档中确认的主要缺口：

- 真实硬件通信未接入：相机、STM32、串口、滤光轮、光源、电机、门控、急停、温度、报警都还是 TODO/MOCK。
- `create_offline_capture_dataset()` 会写模拟 RGB/多光谱/暗白图片，只能用于离线验证。
- 主 UI 的自检按钮、串口刷新、光源切换、紧急停止均为前端模拟日志。
- `quality_algorithm.roi.apply_mask_to_image(registration_mode="calibrated")` 明确抛出 `NotImplementedError`。
- 当前没有真实 Production 模型文件；SSC/TA/pH 默认会 `model_missing`。
- 当前没有提交真实样品图像数据；`sample_data/README.md` 说明不再内置 demo 图像目录。
- Model Studio 已能删除 Sample 记录或同时删除本地托管副本，并保留 `source_path` 原始目录；Dataset 级删除/归档策略仍需确认。
- `model_studio/database/model_studio.sqlite` 当前为空/待初始化，没有真实数据集和模型记录。
- 主程序检测结果没有正式历史记录数据库，仅 UI 状态和文本报告。
- 报告导出是前端 TXT，不是正式 PDF/数据库记录。
- 形态分析当前以像素尺度为主，未完成真实尺寸标定和三维硬件方案。
- 旧 `pipeline_v2.py` 和 `pointcloud_service.py` 中仍有兼容 RGB-D/点云的遗留路径，需避免误认为当前主硬件已经有深度相机。

## 10. 下一阶段开发路线

Phase 1：把离线软件闭环变成可用于真实采集前的数据闭环

- 确定正式样品目录规范和 metadata 字段。
- 替换开发滤光片配置为真实孔位/中心波长/带宽/曝光/增益配置。
- 用真实样品验证 Model Studio 本地托管导入、标签保存、Dataset Version、训练和人工发布流程。
- 用真实小批量数据发布第一版 SSC/TA/pH 其中至少一个 Production 模型。
- 增加检测结果持久化与更可靠报告导出。

Phase 2：接入硬件最小闭环

- 接入 RGB 相机 SDK，完成预览、拍照、保存和曝光/增益锁定。
- 接入黑白相机 SDK，完成单波段拍照和 RAW/位深保存。
- 接入 STM32 串口协议，完成滤光轮 HOME/GOTO、光源开关/亮度、状态读取。
- 将 `create_offline_capture_dataset()` 替换为真实采集流程，同时保留明确的离线调试模式。
- 增加暗场/白板采集 UI 与 metadata 写入。

Phase 3：提升算法、质量控制和产品化

- 完成 RGB 与多光谱图像配准标定。
- 引入真实尺寸标定或三维方案，明确普通形态测算与三维建模边界。
- 增加模型置信度/误差估计、异常样品检测和数据质量门槛。
- 完善历史记录、批次管理、报告模板、权限和操作日志。
- 对 EXE 打包、依赖、启动日志和现场部署做稳定性测试。
