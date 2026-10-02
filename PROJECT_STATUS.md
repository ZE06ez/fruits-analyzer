# Project Status

更新时间：2026-10-03（北京时间）
核对基线：`main@9e23790fd2260b237df1560727cd342b2d6376bc`（PR #41 合并后）。

本文是当前状态摘要。判断优先级：当前代码 > 当前配置/数据库结构 > 当前测试 > 最新项目文档 > 历史文档/聊天。此次为静态代码与文档核对，未连接用户硬件、未读取用户本机运行时数据库、未重新执行完整测试；不新增任何实机 PASS 或科学性能结论。

## 项目目标与总体状态

本项目是一套水果/果实口感与品质多光谱无损检测上位机软件。通过 RGB 彩色相机、DO3THINK/DVP2 黑白相机、滤光片转轮、光源和 STM32 采集样品，完成形态分析、SSC、TA、pH、糖酸比和口感分析。

当前已有采集软件编排、共享分析管线、模型训练/人工发布、检测预测、检测历史和产品化基础。已有功能代码不等于完整实机采集验收通过，也不等于已具备经过真实水果实验验证的 Production 模型。

| 能力 | 当前状态 |
| --- | --- |
| RGB / DVP2 相机接入 | 已有真实 adapter；项目历史记录有用户实机验证，完整现场验收待完成 |
| 单视角 True Capture | 已有 readiness 控制的软件入口；执行取决于当前设备、参数、标定和操作员确认 |
| 多视角 True Capture | 已有编排；真实 SampleStage 协议未知，硬件路径阻塞 |
| 暗白校正、背景分割、RGB→DVP2 配准、保守 ROI | 已有软件实现；真实标定与 ROI 质量待验收 |
| Model Studio 训练→人工发布→检测 | 已有软件链路、输入一致性检查与质量门禁 |
| 真实训练数据 / 正式模型 | 当前仓库未提交可用真实样品或模型 bundle；用户本机数据库与模型状态未核对 |
| 检测历史 | 已有独立 SQLite、列表/详情/归档和溯源 |
| 报告 | 已有前端 TXT 导出；正式 PDF/科研报告未实现 |
| VIP / CARS / SPA 特征波段筛选 | 已核对的训练、特征提取与 Model Studio 入口未发现对应实现 |
| 生产放行 | 当前 readiness 返回 `productionAccepted=false`；验收表关键域尚未全部 PASS |

## 主要目录与模块

以下路径除 `docs/` 和 `camera/` 外，均位于 `host_software/static_ui_prototype_bin/`：

- `backend_server.py`、`index.html`、`styles.css`、`app.js`：本地 Python HTTP 服务与静态前端。
- `camera_service/`：RGB UVC/DirectShow、DVP2 ctypes adapter、科学采集传输分类、相机参数 store、对焦质量辅助。
- `capture_coordinator.py`、`device_manager.py`：采集计划、readiness、取消/超时、Dark/White、RGB、多波段、多视角和安全收尾。
- `stm32_protocol.py`、`stm32_controller.py`、`serial_service.py`：AA55 current firmware profile、命令/状态验证、串口边界。
- `sample_stage.py`：独立样品旋转台软件边界。
- `quality_algorithm/`：暗白校正、背景参考分割、几何配准、ROI、光谱特征、预处理、模型 IO、输入 contract 与质量 policy。
- `training/`、`model_studio/`：PLSR/SVR/RF、数据集/版本/标签/实验/模型生命周期。
- `inspection/`：独立检测历史与 provenance。
- `runtime_support.py`、`launcher.py`、`FruitTasteAnalyzer.spec`：运行时数据目录、迁移/备份、日志、单实例与打包。
- `docs/`：上下文、需求、架构、变更、部署与硬件验收表。
- 根目录 `camera/`：厂商资料，不作为 Python 包，不自动解压或移动。

## 硬件接入与采集边界

### RGB 与 DVP2

RGB 通过 OpenCV/DirectShow `RgbUvcCamera` 接入。历史预览验证为 `device_index=1`、`3840×2160@25fps MJPG`；index 是配置/当前位置，不是跨电脑永久身份。状态区分 detected、available、opened、streaming，probe/停止预览释放句柄后保留已验证的 detected/available。

当前 `config/camera_settings.example.json` 分开保存 preview 与 scientific profile：预览请求 `3840×2160@25fps MJPG`，科学采集请求 `1920×1080@5fps YUY2`。示例配置只代表 requested，设备 actual 必须在运行时回读。

`camera_service/rgb_scientific.py` 当前按实际传输分类：

| actual FOURCC 类型 | 科学采集准入 | 元数据含义 |
| --- | --- | --- |
| MJPG/JPEG/H264/H265 等有损编码 | 拒绝 | lossy，PNG 保存不能消除源端有损 |
| YUY2/YUYV/UYVY | 允许 | `uncompressed_422`、4:2:2、`scientificStrictLossless=false` |
| 代码列入严格无损白名单的传输 | 帧格式同时为 RGB uint8 时允许 | `strict_lossless` |
| 未知/未回读传输 | 拒绝 | transport 未验证 |

因此不能再把“未找到完整 RGB strict-lossless 模式”等同于所有科学采集均被阻断。readiness 使用 `scientificCaptureApproved` 判断准入；仍需实机确认 profile、actual FOURCC 和保存结果。当前部分前端说明仍按 strict-lossless PASS/FAIL 展示，可能与 YUY2 后端准入语义不一致，属于待修正 UI 文案/状态表达。

DVP2 是 DO3THINK/度申 MGV231M-H2 GigE/RJ45 黑白相机，通过 SDK ctypes 枚举/打开/取帧，不用 OpenCV 替代。项目历史记录用户退出 BasedCam3 后验证过 `2048×1200 Mono8 uint8`、30 帧取图和 PNG 保存；当前只验证 Mono8，不开放 PixelFormat 切换。raw mono 正式采集与 latest encoded JPEG 网页预览分离，预览 cache 不进入标定、训练或预测。

后端 `config/camera_settings.json` 是运行时配置（gitignored）；支持应用、应用并保存、保存默认、恢复与 actual 回读。DVP2 只持久化身份、曝光、增益。持久化不代表写入相机 EEPROM/UserSet，设备身份 mismatch 会阻止静默套用配置。

### STM32、滤光轮、光源与样品台

- 默认 STM32 adapter 使用 AA55 current firmware profile，帧为 `AA 55 CMD PLEN PAYLOAD CRCH CRCL`，CRC16-CCITT-FALSE 覆盖 CMD+PLEN+PAYLOAD；旧两字节方法保留兼容边界。
- 命令经过 DeviceManager→Stm32ControllerAdapter→SerialService；ACK 表示命令接受，运动/输出验证需 post-command fresh STATUS。
- 当前 STM32 电机代表 16 孔滤光轮，默认 1600 ppr、22.5°/slot；MOVE payload 为 float32 LE degrees+rpm，主机不发送 pulses。PPR mismatch 只诊断，不自动 SET_CONFIG。
- 手动滤光轮移动默认不重试；ACK OK 但 fresh STATUS 无运动变化会 `motion_not_started` 并 STOP。SET_ORIGIN 是人工对准后的逻辑零点，不是自动 HOME，也不是物理编码器绝对反馈。
- 项目历史记录 PB7/PB8 两路钨灯通过 `LED_SET=0x12`、bit0/bit1 验证；PB9 LED3 使用 bit2。已有风扇、光源互锁、限时钨灯测试与 fresh STATUS 回读；不代表双钨灯、完整照明同步或亮度闭环已验收。
- 推杆 ACK 不代表门到位；没有端点反馈。远程 Fault Clear 不支持。急停/安全收尾已有软件策略，仍需现场证据。
- SampleStage 与滤光轮是独立控制域。样品台已有 status/API/home/move/stop 边界，但默认 `SAMPLE_STAGE_PROTOCOL_UNKNOWN`；真实控制板协议、HOME、位置回读和 fault contract 未补齐，禁止复用滤光轮电机。

## 样品与采集流程

新建样品与真实采集放行分开。创建样品校验名称、果种/品种和保存目录，不要求先有模型或已连接全部硬件。

- `sample_mode=inspection`：正常检测，使用人工发布的 Published/Default 模型和 generic 品种兜底。
- `sample_mode=training_capture`：仅采集训练数据，允许手输新果种/品种，不绑定 SSC/TA/pH 模型；采后导入 Model Studio 并补充实测标签。
- `POST /api/capture/start`：True Hardware Capture，调用当前计划 readiness 后执行可选 Dark/White、RGB、DVP2 多波段、metadata、安全收尾。单视角不要求真实 SampleStage，多视角仍受样品台协议阻塞。
- `/api/complete-capture`：Offline/Demo，调用 `create_offline_capture_dataset()` 生成模拟数据，不代表真实采集。
- `devicePrepared` 与 `trueCapturePrepared` 含义不同；后者是当前计划 readiness，不能固定写成 false，也不能作为实机验收 PASS。当前仍有 `HARDWARE_ACCEPTANCE_NOT_PASSED` warning 与 `productionAccepted=false`。
- 正式采集输出 PNG，并记录 requested/actual、源传输、科学采集质量类型、校正/波段/设备身份及失败/partial 状态。
- 保存父目录和 RGB/多光谱子目录名可配置，写入 `metadata.json.image_directories`；本次拍摄自动导入，其他样品先选父目录再确认子目录。默认仍为 `rgb/`、`multispectral/`。

正式验收依据为 `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`。当前表中多数域为 NOT TESTED、真实样品台为 BLOCKED；本次文档核对不改变这些结果。

## 分析、训练与模型发布

P1C-2A/2B 已实现 RGB→DVP2 平面单应性标定及保守 registered multispectral ROI；先将 RGB Fruit Mask warp 到 DVP2 坐标，再 inward erosion。不是三维果面逐像素精确配准；真实 profile、overlay 与几何误差仍需验收。

P1E-1/2 的 `run_feature_pipeline()` 是训练与检测共享入口。production 默认要求 Dark/White、Background Reference、calibrated registration、registered ROI 和完整启用波段；缺失依赖明确失败，legacy/development 需要显式配置。`model_input_contract` / `pipeline_signature` 检查标定、分割、配准、波长、ROI 与特征 schema，防止训练/预测输入语义不一致。

Model Studio 已支持本地托管样品、标签、Include/Exclude、引用保护删除、Archive、不可变 Dataset Version、Diff、单 target Experiment/Run/Variant、Retrain lineage、Candidate/Validated/Published/Archived、人工 Publish/Default。训练不会自动替换正式模型；Published/Default 需通过 production contract 与 Model Quality Gate。默认质量 policy 是软件检查参数，不能当作最终科学阈值。

当前训练支持 RAW/SNV/MSC 与 PLSR/SVR/RF；验证支持按 sample_id 的 GroupKFold（最多 5 折）或 grouped holdout，预处理在训练 fold 内拟合。仍有以下研究缺口：

- `training/train.py::fit_regressor()` 的 PLSR 成分数在 1～min(特征数,训练行数−1,10) 内按训练误差选择，尚未实现内层 CV 调参。已有外层分组验证，不能写成“没有交叉验证”。
- 指标已有 R²、RMSE、MAE、RPD；未实现 MAPE / 100%−MAPE。后者如后续增加，需明确为相对误差衍生指标。
- 已核对的入口未发现 VIP/CARS/SPA、连续全光谱研究流程、筛选稳定性统计或滤光片候选方案生成；这些是后续研究模块，未在本次更新中实现。
- 今后若加入监督波段筛选，应在训练 fold 内执行并结合内层调参；最终硬件方案需验证中心波长、带宽、相机响应、光源和真实多光谱重采数据，不能只把相近 nm 取平均后直接采购。
- `quality_algorithm/filters.py` 默认读取 `filter_config.development.json`；该开发配置只启用 450/560/670 nm，730/850/940 nm 未启用。这不是最终硬件波段方案；本次不修改启用状态。

当前仓库树中未提交 `trained_models/<target>/model.joblib` 与 `metadata.json` 或真实样品图像。不能由此判断用户本机是否已有数据集、数据库或模型。缺模型返回 `model_missing`，缺分析依赖或输入 contract 不匹配分别明确失败，不生成假预测。

## 检测记录与产品化

- P1E-4 已有独立 `inspection/inspection.sqlite`、Inspection/Prediction Result、列表/详情/归档和结果区历史查看，记录样品/源文件/标定/配准/管线/模型 provenance；不是未实现。
- 报告导出目前为 `fruit_quality_report.txt`。正式 PDF/科研报告、批次管理及更完整模板仍待开发；当前 TXT 中关于 CaptureCoordinator 尚未接入的旧说明也需要后续改正。
- P1E-5 已有可配置模型质量门禁与报告，发布/设默认会检查，但真实水果误差、OOD 与科学放行标准仍待实验。
- P1E-6 已有 SQLite 版本迁移/备份、重启任务恢复、配置 bootstrap、atomic JSON、轮转日志、health endpoint、Windows 无硬件 CI 与 PyInstaller smoke 代码。
- Windows launcher 使用 Named Mutex 单实例；冻结 EXE 可变数据默认放在 `%LOCALAPPDATA%\FruitTasteAnalyzer\app_data`，可由 `FRUIT_TASTE_ANALYZER_RUNTIME_DIR` 覆盖。这是产品化软件基础，不代表最新 EXE 或硬件已验收。

## 当前 UI

设备入口收拢为唯一“设备与维护”，按系统摘要、核心设备、光学系统、运动系统呈现；手动动作与 raw diagnostics 收入 Inspector / Advanced。保留 Operator/Engineer 分离和 Dark Slate + Purple/Fuchsia/Cyan 配色。

左侧持久流程为“样品→设备→标定→RGB→多光谱→分析→结果”；当前状态主要使用 completed/current/pending，blocked 样式已有，但流程阻塞表达仍可完善。硬件按钮使用 ready/blocked/running：blocked 可点击查看原因且前端不发命令，running 才 disabled；后端 interlock/readiness 仍是最终边界。

分析页隐藏双相机预览，保留 RGB 二维形态/表面分析；尺寸仍以像素尺度为主，真实尺寸标定/三维硬件方案未完成。预处理、ROI 与特征合并为分析步骤入口。

## 下一步

1. 先补研究有效性：PLSR 内层分组 CV、真实标签/异常样品核查、独立验证；如增加波段筛选，再落实 fold 内筛选与稳定性记录。
2. 收集连续光谱、样品对应 SSC/TA/pH 实测值和具体筛选波长，评估滤光片候选；最终用真实多光谱硬件重采数据验证。
3. 在具备现场条件时逐项填写硬件验收表；单视角 readiness 软件可执行与正式生产放行继续分开。
4. 补齐真实 SampleStage 控制器协议，完成几何/ROI/暗白/照明验收和正式实测滤光轮配置，再训练并人工发布真实模型。
5. 修正 RGB YUY2 准入前端说明和 TXT 旧文案，完善正式报告与现场部署验证。

在暂不能实机验收时，继续使用明确标注的离线数据验证数据资产、冻结版本、训练/发布保护和检测历史，不把模拟结果写成实机完成。
