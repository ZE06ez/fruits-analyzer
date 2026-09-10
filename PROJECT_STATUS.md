# Project Status

更新时间：2026-09-10

## 项目目标

本项目是一套水果/果实口感与品质多光谱无损检测上位机软件。目标是在暗箱内通过 RGB 彩色相机、DO3THINK/DVP2 黑白多光谱相机、滤光片转轮、光源和 STM32 控制板完成样品采集，再进行形态分析、SSC/糖度、TA、pH、糖酸比和口感分析。

## 当前主要目录与模块

- `host_software/static_ui_prototype_bin/`：当前主程序，包含静态前端、Python 本地 HTTP 后端、硬件控制、相机服务、形态分析、预测入口、训练与 Model Studio。
- `host_software/static_ui_prototype_bin/camera_service/`：RGB UVC 相机、DVP2 多光谱黑白相机接入层，以及后端权威相机参数持久化 store。
- `host_software/static_ui_prototype_bin/model_studio/`：数据集、标签、特征、训练实验、候选模型、发布模型管理。
- `host_software/static_ui_prototype_bin/quality_algorithm/`：多光谱校正、ROI、特征、预处理和模型 IO。
- `host_software/static_ui_prototype_bin/training/`：PLSR/SVR/RF 训练与评估。
- `docs/`：长期项目上下文、需求、架构、变更记录。
- `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`：P1B 真实硬件与采集系统正式现场验收规范，用于逐项记录测试人员、日期、硬件身份、实测值、证据、PASS/FAIL/BLOCKED 和 true capture 放行决定。
- `camera/`：厂商资料目录，不作为 Python 包，不应改名或移动。

## 当前硬件接入状态

- RGB 彩色相机：已通过 OpenCV/DirectShow `RgbUvcCamera` 接入，当前电脑验证默认 `device_index=1`、`MJPG`、`3840x2160`、`25fps`，可预览和应用参数；P1B-3 已在 CaptureCoordinator 受保护路径中接入正式单帧 RGB PNG 保存，P1B-8 已把单视角 True Capture 接到 `/api/capture/start` 的软件编排入口，但真实硬件仍需 readiness 和 acceptance gate。P1B-3.7 设备选择层会尝试读取 Windows PnP/FriendlyName/VID/PID/USB serial 等信息；只有可靠映射才生成 stableId，DirectShow index 仍只是 last known location。P1B-8.1 后相机设置新增后端权威 `config/camera_settings.json`，区分 Apply、Apply & Save、Save Default、Restore Saved、Restore Default；RGB 持久化 device identity/index、width/height/fps/fourcc、auto/manual exposure、gain、white balance、requested/actual/settingResults，启动或重新打开相机后按 store 恢复并回读，设备身份不一致返回 `CAMERA_SETTINGS_DEVICE_MISMATCH`。RGB 网页预览由后台 latest-frame + latest encoded JPEG cache 服务；正式 RGB capture 仍直接取 `CameraFrame` 并保存 PNG，不读取预览 JPEG。
- 多光谱黑白相机：DO3THINK/度申 MGV231M-H2 GigE/RJ45，已基于 DVP2 SDK `ctypes` 接入 adapter；用户已确认退出 BasedCam3 后 manual test 可打开、取流、30 帧取图和保存 PNG；主 UI 已接入 DVP2 重新检测、低延迟网页预览、曝光/增益下发和回读；P1B-8.1 后 DVP2 exposure/gain 可 Apply & Save 到后端 store，并在 probe、preview/capture open、显式 restore、reconnect 后通过 `set_exposure()`/`set_gain()` 再 `get` 回读记录 actual；只持久化 device identity、exposure、gain，不开放 PixelFormat/trigger/ROI 持久化。P1B-4/P1B-5/P1B-6/P1B-7 已具备 raw mono 单帧、多波段 sequence、Dark/White reference 和 Sample MultiView 软件路径；P1B-8 已把这些能力编排到单视角 True Capture 软件入口，支持 existing CalibrationSet 或重新采集 Dark/White 后再采 sample。暗白物理校正、滤光轮/光源现场验收和真实样品台硬件仍未 PASS。
- STM32/串口：已有两字节协议、串口连接、PING、急停、风扇、推杆/门控、LED3 和滤光轮基础控制层；P1B-7.5A.1 已绑定当前 STM32 firmware AA55 profile，P1B-7.5A.2 已新增 Web 硬件调试控制与 fresh STATUS 运动验证。`stm32_protocol.py` 固化 `CURRENT_STM32_FIRMWARE_PROFILE`：`AA 55 CMD PLEN PAYLOAD CRCH CRCL`、CRC16-CCITT-FALSE 覆盖 `CMD+PLEN+PAYLOAD`、ACK echo/result、STATUS 18 bytes、INFO 13 bytes、命令号 `MOVE_ABS=0x01`、`MOVE_REL=0x02`、`STOP=0x03`、`QUERY_STATUS=0x08`、`SET_ORIGIN=0x09`、`FAN_SET=0x10`、`DOOR_SET=0x11`、`LED_SET=0x12` 等。`stm32_controller.py` 处理 ACK echo correlation、STATUS/INFO cache revision、handshake、PPR consistency diagnostic、command serialization、status-aware mechanical verification 和 best-effort safe stop。默认生产 adapter 使用 AA55 current profile，旧两字节/short-frame 仅保留兼容测试和边界。当前 firmware 不支持远程 Fault Clear，UI 已禁用并提示 unsupported。
- 滤光片转轮：已有 HOME/相对旋转控制入口；P1B-5 软件侧已通过现有 `HardwareController.wheel_home()`、`wheel_move_relative()`、`get_wheel_status()` 接入 DVP2 多波段序列；P1B-7.5A.1 的 current mapping 为 16 孔、1600 ppr、22.5°/slot、100 pulses/slot 仅作诊断，主机对 MOVE_REL/MOVE_ABS 发送 float32 LE degrees + rpm（默认 10 rpm），不发送 pulses。P1B-7.5A.2 后 Web 手动移动只发送方向 + 正 slot 数，后端映射 counterclockwise=正 slot、clockwise=负 slot，默认不 retry；完成判定必须依赖 post-command fresh STATUS，ACK OK 但 target/position 未变化会失败为 `motion_not_started` 并 STOP。PPR mismatch 只报告 `filter_wheel_ppr_mismatch` 并要求人工确认，不自动 SET_CONFIG。当前 SET_ORIGIN 只表示人工对准后建立逻辑 0°，不是自动 HOME sensor；STATUS position 是逻辑位置，不等于 CL57C physical encoder absolute feedback。
- 样品旋转平台：P1B-7.5B 已把 `SampleStage` 扩展为独立样品旋转台 software boundary：`sample_stage.py` 现在有统一 status model、connect/home/move_to/move_relative/stop/get_status/get_position/safe_stop 接口和默认 `SAMPLE_STAGE_PROTOCOL_UNKNOWN` 边界；`DeviceManager` 和后端新增 `/api/device/sample-stage/status|home|move-absolute|move-relative|stop`，主 UI 设备准备页新增样品旋转台调试区。仓库内未找到独立样品台控制板、COM/baudrate、命令格式、HOME 传感器、position feedback 或 fault contract 的真实资料，且现有资料明确当前 STM32 motor 只代表滤光轮；因此真实样品台仍为 BLOCKED — PROTOCOL UNKNOWN，不能标记为硬件完成。
- 标定配准：暗/白校正算法已有；RGB 与多光谱几何配准仍未完成。

## 当前采集流程

P1B-8 后主流程分为两条清晰路径：`/api/complete-capture` 只保留 Offline/Demo 数据生成，继续调用 `create_offline_capture_dataset()`；`POST /api/capture/start` 成为 True Hardware Capture 的正式启动入口，先按 `TrueCapturePlan` 做 readiness，再复用已有 Dark/White、RGB PNG、DVP2 raw mono PNG、多波段 sequence 和 Sample MultiView 编排。单视角 True Capture 不要求真实 SampleStage；真实多视角 True Capture 因 `SAMPLE_STAGE_PROTOCOL_UNKNOWN` 在 precheck/readiness 阶段 BLOCK。`trueCapturePrepared` 不再表示“代码写完”，而表示当前 capture plan 是否满足真实执行条件，且仍带 `HARDWARE_ACCEPTANCE_NOT_PASSED` warning。正式 scientific capture 文件统一为 lossless PNG；RGB/DVP2 latest-frame JPEG cache 只用于浏览器 preview，不能进入 dataset、calibration、feature extraction 或 prediction。

P1B-8.1 后正式 capture metadata 会额外记录 `settingsSource`、`settingsRestoreState`、`requestedSettings` 和 `actualSettings`。无后端保存配置时 `settingsSource=default` 不构成 true capture blocker；如果已保存配置恢复失败会给 readiness warning，设备 identity mismatch 则作为 `CAMERA_SETTINGS_DEVICE_MISMATCH` blocker，避免把 A 设备配置静默套到 B 设备。

## 当前设备发现与选择状态

P1B-3.7 后，设备发现和设备检查按 STM32、RGB、DVP2 三个硬件域独立执行。缺少 pyserial 会报告 `dependency_missing`，不会阻断 RGB/DVP2 检查；STM32 未连接时，相机检查仍会继续。设备准备页和相机设置页共享候选下拉框与绑定状态，RGB 绑定会更新运行时 `deviceIndex`，DVP2 绑定会更新运行时 stable identity。DVP2 仍只走 DVP2 SDK/ctypes 枚举与打开，不使用 OpenCV，也不根据普通网卡存在推断相机在线。

## 当前 RGB / 多光谱目录逻辑

保存时用户先选择保存父目录，再确认 RGB 与多光谱图像子目录名称。默认兼容旧结构：

- RGB：`rgb`
- 多光谱：`multispectral`

用户可改成自定义目录名，实际名称会写入 `metadata.json.image_directories`。本次拍摄分析优先使用 session/metadata 中的实际目录名；手动分析其他样品时先选择父目录，再扫描一级子目录，由用户选择 RGB、多光谱和其他目录。

## 当前模型训练状态

Model Studio 已支持 Dataset、本地托管样品、后续 Add Samples、`labels.csv`/手动标签、Include/Exclude 噪声样品、引用保护的 Sample Permanent Delete、Dataset Archive、带生产模型依赖保护的 Dataset Permanent Delete、数据质量检查、不可变 Dataset Version、Version Diff、特征提取、Training Experiment / Run / Model Variant、PLSR/SVR/RF、RAW/SNV/MSC、模型比较、Candidate/Validated/Published/Archived 生命周期、Published 模型手动选择、Default 模型自动选择、Retrain lineage 和引用保护的 Model Permanent Delete。Dataset Permanent Delete 会阻止 Published/Default/Production 模型引用，允许级联清理 Candidate/Validated/Archived 实验垃圾数据，并只删除 Model Studio 受管目录。主程序可按水果/品种/target 选择 Default 或 generic fallback 模型，Published 非 Default 模型也可手动选择；Archived 模型不进入检测工作站自动/手动选择。当前仓库没有可直接上线的真实 Production 模型，缺模型时预测返回 `model_missing`，不会生成假数值。

## 当前 UI 状态

主程序是静态 HTML/CSS/JS + Python 本地 HTTP 服务。已有统一系统状态、一键设备检查、采集/分析布局切换、相机设置页、形态分析、SSC/TA/pH 分析入口、糖酸比口感分析和 Model Studio。P1B-8.1 后相机设置页从 `/api/camera/settings` 读取后端保存的 requested settings，旧 `fruitAnalyzer.cameraSettings` 只作为 UI cache/一次性迁移来源；按钮区明确区分“应用到相机”“应用并保存”“保存为默认配置”“恢复已保存配置”“恢复默认配置”，并显示已保存 requested、当前 actual 和 restore state。P1B-8 在样品采集页新增 True Hardware Capture 小面板：采集模式、校正模式、Calibration ID、暗场/白板操作员确认、开始真实采集、取消采集和 readiness 刷新；多视角在 SampleStage 协议未知时禁用/提示。旧分步“完成采集”仍用于 Offline/Demo。设备准备页的样品旋转台调试区默认因 `SAMPLE_STAGE_PROTOCOL_UNKNOWN` 不可用，不会把滤光轮电机当作样品台。

## 当前开发重点

下一阶段重点应在 P1B-7.5A.2 已提供 Web 硬件调试入口和 P1B-7.5B 样品旋转台 software boundary 的基础上，按 `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md` 做真实硬件现场验收：fresh STATUS 查询、fan duty on/off、LED3 mask 0x04/0x00、推杆伸出/缩回/停止的 command accepted 与后端定时 STOP、滤光轮 +22.5°/-22.5° 低速运动的 fresh STATUS 验证、人工 SET_ORIGIN、RGB/DVP2 预览延迟、Dark/White、多波段、metadata、PNG integrity 和 safe_stop 策略。样品旋转台仍是独立控制板，不属于当前 STM32 滤光轮电机；下一步需要补齐真实样品台控制器资料后才能实现 hardware adapter。需要继续保持 `devicePrepared` 与 `trueCapturePrepared` 的语义区分，不能因为受保护内部方法可用就放行完整真实采集。

Model Studio 侧当前已整理为 Dashboard / Datasets / Training / Models / Settings 信息架构。科研数据维护原则为 Working Dataset 可编辑、Dataset Version 冻结不可变；新增样品、标签修改和 Exclude 会让 Working Dataset 标记 dirty，训练必须使用 Dataset Version。旧版本、实验和模型 lineage 引用过的样品不能物理删除，只能从后续版本中 Exclude。Dataset Archive 保留全部历史；Dataset Permanent Delete 仅用于清理无生产引用的实验垃圾数据，删除前会列出 samples / versions / experiments / jobs / models，并保护 Published / Default 模型引用。

## 已知问题

- P1B-8 True Capture orchestration 已软件实现：单视角路径可按 readiness 依次执行 precheck、可选 Dark/White、RGB、DVP2 多波段、metadata 和安全收尾；但真实硬件 acceptance 仍未 PASS，不能写成完整硬件采集已验收。
- P1B-8.1 Camera Settings Persistence 已软件实现：后端配置文件负责 Apply/Persist/Restore/Readback 闭环，但这只是上位机本地配置持久化，不代表 RGB 或 DVP2 参数写入相机 EEPROM/UserSet。
- P1B 真实硬件与采集系统验收规范已建立在 `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`；当前只是验收表建立，不代表任何未现场测试硬件已经 PASS。
- RGB latest-frame 预览已在当前电脑做 CLI benchmark，仍建议从主程序相机设置页做浏览器端现场复核，重点观察 sourceAge、server、fetch、FPS 和 drop 计数。
- DVP2 低延迟网页预览需要在设备在线且 BasedCam3 完全退出后做现场端到端复核；本轮 DVP2 已改为 latest encoded JPEG cache 并由 fake adapter 测试覆盖，当前环境 DVP2 枚举返回 0。
- 多光谱 PixelFormat 当前只验证 `Mono8`，不开放切换。
- 滤光轮、光源、相机曝光、样品旋转已有软件编排边界；P1B-7.5B 搜索仓库后确认真实样品台控制协议未知，`SAMPLE_STAGE_PROTOCOL_UNKNOWN` 阻塞真实 adapter；滤光轮/光源现场同步验收尚未完成。
- P1B-7.5A.2 已在软件中完成 Web 硬件调试入口和 fresh STATUS 运动验证；仍未做真实 STM32 硬件 smoke，因此 `currentFirmwareProfileValidated` 只有在串口 handshake 读到合法 STATUS 后才会为 true，fan/LED3/推杆/滤光轮控制仍需现场验证。
- RGB 与多光谱几何配准、尺寸标定未完成。
- 当前没有真实训练数据和正式 Production 模型。
- 检测历史数据库和正式报告导出未实现。
