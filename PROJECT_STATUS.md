# Project Status

更新时间：2026-09-06

## 项目目标

本项目是一套水果/果实口感与品质多光谱无损检测上位机软件。目标是在暗箱内通过 RGB 彩色相机、DO3THINK/DVP2 黑白多光谱相机、滤光片转轮、光源和 STM32 控制板完成样品采集，再进行形态分析、SSC/糖度、TA、pH、糖酸比和口感分析。

## 当前主要目录与模块

- `host_software/static_ui_prototype_bin/`：当前主程序，包含静态前端、Python 本地 HTTP 后端、硬件控制、相机服务、形态分析、预测入口、训练与 Model Studio。
- `host_software/static_ui_prototype_bin/camera_service/`：RGB UVC 相机和 DVP2 多光谱黑白相机接入层。
- `host_software/static_ui_prototype_bin/model_studio/`：数据集、标签、特征、训练实验、候选模型、发布模型管理。
- `host_software/static_ui_prototype_bin/quality_algorithm/`：多光谱校正、ROI、特征、预处理和模型 IO。
- `host_software/static_ui_prototype_bin/training/`：PLSR/SVR/RF 训练与评估。
- `docs/`：长期项目上下文、需求、架构、变更记录。
- `camera/`：厂商资料目录，不作为 Python 包，不应改名或移动。

## 当前硬件接入状态

- RGB 彩色相机：已通过 OpenCV/DirectShow `RgbUvcCamera` 接入，当前电脑验证默认 `device_index=1`、`MJPG`、`3840x2160`、`25fps`，可预览和应用参数；P1B-3 已在 CaptureCoordinator 受保护路径中接入正式单帧 RGB PNG 保存，但完整 `/api/capture/start` 仍未放行。P1B-3.7 设备选择层会尝试读取 Windows PnP/FriendlyName/VID/PID/USB serial 等信息；只有可靠映射才生成 stableId，DirectShow index 仍只是 last known location。
- 多光谱黑白相机：DO3THINK/度申 MGV231M-H2 GigE/RJ45，已基于 DVP2 SDK `ctypes` 接入 adapter；用户已确认退出 BasedCam3 后 manual test 可打开、取流、30 帧取图和保存 PNG；主 UI 已接入 DVP2 重新检测、低延迟网页预览、曝光/增益下发和回读；P1B-5.4 后多光谱预览由后台 latest-frame cache 驱动，HTTP 预览请求只编码最新可用帧并显示 frameId/耗时/FPS 诊断，允许丢弃旧帧以降低延迟；P1B-4 已在 CaptureCoordinator 受保护路径中接入 DVP2 raw mono 单帧 PNG 保存，保留 `uint8/uint16` 灰度数据并记录未分配波段 metadata；P1B-5 已新增滤光轮 + DVP2 多波段同步采集软件路径，按 filter config 采集每个 enabled band 的 raw PNG；P1B-6 已新增 Dark/White 多波段参考采集软件路径，保存 raw `calibration/dark|white/band_XX_<bandId>.png`，写入 `CalibrationSet`、`calibrationId`、band/exposure/gain 匹配信息和诊断；P1B-7 已新增样品多视角软件编排，每个 View 采 RGB + 多光谱 sequence 并引用同一 CalibrationSet。暗白物理校正、真实样品台硬件和完整 `/api/capture/start` 仍未放行。
- STM32/串口：已有两字节协议、串口连接、PING、急停、故障清除、风扇、门、光源和滤光轮基础控制层；P1B-7.5A.1 已绑定当前 STM32 firmware AA55 profile。`stm32_protocol.py` 固化 `CURRENT_STM32_FIRMWARE_PROFILE`：`AA 55 CMD PLEN PAYLOAD CRCH CRCL`、CRC16-CCITT-FALSE 覆盖 `CMD+PLEN+PAYLOAD`、ACK echo/result、STATUS 18 bytes、INFO 13 bytes、命令号 `MOVE_ABS=0x01`、`MOVE_REL=0x02`、`STOP=0x03`、`QUERY_STATUS=0x08`、`FAN_SET=0x10`、`DOOR_SET=0x11`、`LED_SET=0x12` 等。`stm32_controller.py` 处理 ACK echo correlation、STATUS/INFO cache、handshake、PPR consistency diagnostic、command serialization、status-aware mechanical retry 和 best-effort safe stop。默认生产 adapter 使用 AA55 current profile，旧两字节/short-frame 仅保留兼容测试和边界。
- 滤光片转轮：已有 HOME/相对旋转控制入口；P1B-5 软件侧已通过现有 `HardwareController.wheel_home()`、`wheel_move_relative()`、`get_wheel_status()` 接入 DVP2 多波段序列；P1B-7.5A.1 的 current mapping 为 16 孔、1600 ppr、22.5°/slot、100 pulses/slot 仅作诊断，主机对 MOVE_REL/MOVE_ABS 发送 float32 LE degrees + rpm（默认 10 rpm），不发送 pulses。PPR mismatch 只报告 `filter_wheel_ppr_mismatch` 并要求人工确认，不自动 SET_CONFIG。当前 SET_ORIGIN 只表示人工对准后建立逻辑 0°，不是自动 HOME sensor；STATUS position 是逻辑位置，不等于 CL57C physical encoder absolute feedback。
- 样品旋转平台：已有软件角度计划、metadata/views 记录和 P1B-7 `SampleStage` 高层抽象；MultiView 软件编排可用 fake/simulation 测试，真实样品台电机协议和现场验收未接入。
- 标定配准：暗/白校正算法已有；RGB 与多光谱几何配准仍未完成。

## 当前采集流程

当前主流程仍是离线验证流程，不是真实完整采集。用户完成设备准备后创建样品，系统创建样品目录和 metadata；完成采集时仍调用 `create_offline_capture_dataset()` 生成测试图像。真实相机预览、受保护 RGB 单帧保存、受保护 DVP2 raw mono 单帧保存、受保护滤光轮+DVP2 多波段 sample sequence、受保护 Dark/White 多波段 calibration sequence，以及受保护 Sample MultiView 软件编排已可用，但 `/api/capture/start` 仍由 `CameraIntegrationRequired` 保护，`trueCapturePrepared` 保持 `false`。P1B-5.4 的 latest-frame cache 只用于浏览器 preview，不能作为正式 scientific capture 输入；P1B-6 的 Dark/White 保存使用正式 raw scientific frame，不使用 preview JPEG；P1B-7 的 MultiView 引用同一个 `calibrationId`，不会为每个 View 重拍 Dark/White。

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

主程序是静态 HTML/CSS/JS + Python 本地 HTTP 服务。已有统一系统状态、一键设备检查、采集/分析布局切换、相机设置页、形态分析、SSC/TA/pH 分析入口、糖酸比口感分析和 Model Studio。相机设置页已有 RGB 与多光谱分栏，支持预览、参数应用和设备详情；多光谱预览信息栏会显示 frameId、source timestamp、capture/resize/JPEG/server/browser fetch 耗时和 measured FPS，便于现场判断慢半拍来自采集、编码还是浏览器链路。正式采集工作流仍未改造成真实硬件协调流程。

## 当前开发重点

下一阶段重点应在 P1B-7.5A.1 已绑定 profile 的基础上，做真实滤光轮 AA55 通信、fan duty、LED3、door actuator、safe_stop 和 +22.5°/-22.5° 低速运动的现场验收。样品旋转台仍是独立控制板，不属于当前 STM32 滤光轮电机；P1B-7.5B 之后再单独接入。需要继续保持 `devicePrepared` 与 `trueCapturePrepared` 的语义区分，不能因为受保护内部方法可用就放行完整真实采集。

Model Studio 侧当前已整理为 Dashboard / Datasets / Training / Models / Settings 信息架构。科研数据维护原则为 Working Dataset 可编辑、Dataset Version 冻结不可变；新增样品、标签修改和 Exclude 会让 Working Dataset 标记 dirty，训练必须使用 Dataset Version。旧版本、实验和模型 lineage 引用过的样品不能物理删除，只能从后续版本中 Exclude。Dataset Archive 保留全部历史；Dataset Permanent Delete 仅用于清理无生产引用的实验垃圾数据，删除前会列出 samples / versions / experiments / jobs / models，并保护 Published / Default 模型引用。

## 已知问题

- 完整正式真实采集保存未实现，当前完成采集仍是离线验证数据；RGB 和 DVP2 已分别具备 Coordinator 内部受保护单帧保存路径，DVP2 多波段 sample sequence、Dark/White calibration sequence 和 Sample MultiView orchestration 已软件实现但硬件验收待完成。
- DVP2 低延迟网页预览需要在设备在线且 BasedCam3 完全退出后做现场端到端复核；Codex 本轮只能完成本机 2048x1200 Mono8 编码微基准和 fake adapter 生命周期测试，当前环境 DVP2 枚举返回 0。
- 多光谱 PixelFormat 当前只验证 `Mono8`，不开放切换。
- 滤光轮、光源、相机曝光、样品旋转已有软件编排边界；真实样品台协议、滤光轮/光源现场同步验收尚未完成。
- P1B-7.5A.1 已按用户提供的 current firmware profile 完成上位机绑定和测试；仍未做真实 STM32 硬件 smoke，因此 `currentFirmwareProfileValidated` 只有在串口 handshake 读到合法 STATUS 后才会为 true，真实硬件集成状态仍需现场验证。
- RGB 与多光谱几何配准、尺寸标定未完成。
- 当前没有真实训练数据和正式 Production 模型。
- 检测历史数据库和正式报告导出未实现。
