# Requirements

更新时间：2026-09-10

本文档集中记录当前已经确认或待确认的需求。状态含义：

- 已实现：当前代码中有真实功能。
- 部分实现：有 UI/API/算法骨架，但缺少真实硬件、真实数据、生产模型或完整闭环。
- 未实现：当前代码没有对应实现。
- 模拟：当前仅用于离线演示/调试，不代表真实功能。

## 已确认需求

| 需求 | 状态 | 说明 |
| --- | --- | --- |
| 软件应是水果/果实口感多光谱无损检测系统 | 部分实现 | 主界面、样品流程、形态、模型入口、STM32 硬件控制层、RGB UVC adapter、DVP2 adapter 和 P1B-8 True Capture 单视角软件入口已存在；硬件 acceptance、真实样品台、多视角硬件采集、生产模型和检测历史仍未完成 |
| 使用 RGB 彩色相机采集外观图像 | 部分实现/实机验证 | UI 和目录支持默认 `rgb/`，并支持用户自定义 RGB 子目录名；`RgbUvcCamera` 已在当前电脑通过 OpenCV DirectShow/UVC 验证 `device_index=1`、`MJPG`、`3840x2160`、`25fps`，可返回 RGB `uint8` 帧；相机设置页已支持 `/api/camera/rgb/probe` 真实重新检测、真实参数应用和低延迟 960x540 JPEG 预览；P1B-3 已接入 `CameraManager.capture_rgb_frame()` 和正式 RGB PNG 保存；P1B-8 后单视角 `/api/capture/start` 可复用该正式保存路径，但仍受 readiness/acceptance gate 约束 |
| 使用黑白相机 + 滤光片转轮采集多光谱图像 | 部分实现/实机验证 | 目录和算法支持默认 `multispectral/`，并支持用户自定义多光谱子目录名；滤光轮已有 STM32 HOME/相对旋转控制层；目标黑白相机是 DO3THINK/度申 GigE/RJ45 工业相机，`Dvp2MonoCamera` 已基于真实 DVP2 header/examples 完成 Python 3.12 `ctypes` 绑定；用户已确认完全退出 BasedCam3 后 manual test 可打开、参数读取、取流、30 帧取图和 PNG 保存；网页相机设置页已接入 DVP2 重新检测、实时预览、曝光/增益应用和回读；P1B-4/P1B-5/P1B-6/P1B-7 已接入 DVP2 raw mono 单帧、多波段 sequence、Dark/White reference 和 Sample MultiView 软件编排；P1B-8 后单视角 `/api/capture/start` 可复用这些路径；真实样品台、多视角硬件采集和现场 acceptance 仍未完成 |
| 使用封闭暗箱和稳定光源 | 部分实现 | 升降门、RGB LED 两路、钨灯两路、风扇已有 STM32 控制命令；P1B-2 已在 CaptureCoordinator 中接入安全准备链，可按 RGB/多光谱模式准备互斥光源并确认 interlock；P1B-3/P1B-4/P1B-5/P1B-6 的受保护 RGB/DVP2 单帧、多波段 sample 和 Dark/White reference 保存会复用对应安全准备和关灯收尾；Dark reference 会先关采集光源并通过 output status 验证关闭；亮度闭环和完整真实采图同步未实现 |
| 支持暗场和白板校正 | 部分实现/SOFTWARE IMPLEMENTED | 算法有反射率校正；P1B-6 已在 CaptureCoordinator 中新增 `run_dark_reference_capture()` 和 `run_white_reference_capture()`，复用 `MultispectralCapturePlan`/`MultispectralBandPlan`、滤光轮同步、每 band exposure/gain 设置和 raw `uint8/uint16` PNG saver，写入 `CalibrationSet`、`calibrationId`、`captureType`、完整性和诊断 metadata；物理遮光/标准白板放置和真实校准质量仍需现场验收 |
| 新建样品与真实采集启动必须分离 gate | 已实现/部分真实 | “新建样品”只校验样品名称、果种/品种和保存根目录，可在硬件未连接时先建样品目录；Offline/Demo 完成采集仍保留设备准备边界。True Hardware Capture 入口通过 `DeviceManager.capture_readiness()` 动态检查 sample/output、STM32、RGB、DVP2、滤光轮、CalibrationSet、操作员确认和 SampleStage，并在未就绪时返回所有阻塞原因；`trueCapturePrepared` 只表示当前 plan readiness，不表示 hardware acceptance PASS |
| 真实采集放行必须经过正式 hardware acceptance gate | 已确认/文档已建立 | P1B 正式验收规范位于 `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`，要求逐项记录测试日期、测试人员、硬件身份、实测值、证据、PASS/FAIL/BLOCKED 和 release decision。没有真实硬件证据的项目默认 `NOT TESTED`；unit test、fake adapter 或 simulation 不能把 STM32、风扇、LED、推杆、滤光轮、RGB 浏览器端延迟、DVP2 网页预览、Dark、White、样品旋转台或完整真实采集标记为 PASS |
| 主界面应显示统一全局系统状态 | 已实现 | 顶栏新增“当前状态”，由 `deriveSystemStatus()` 基于设备、样品、离线验证、形态任务和预测状态派生 |
| 设备准备页应提供普通用户的一键设备检查 | 已实现/部分真实 | “开始设备检查”调用 `/api/device/check`，按 STM32、RGB、DVP2 独立硬件域检查；STM32 未连接或缺 pyserial 不阻断 RGB/DVP2；RGB 状态来自 OpenCV/DirectShow adapter probe，多光谱来自 DVP2 SDK probe，标定显示需要确认 |
| 软件应支持统一设备发现、选择和绑定 | 部分实现 | P1B-3.6 新增基础层；P1B-3.7 完善 `/api/devices/discover`、`/api/devices/bindings`、`/api/devices/bind` 的角色/kind 校验、Windows RGB 身份元数据和相机设置页绑定入口。绑定保存 stableId 和 last known location，但不等于 connected/verified/ready |
| 软件不得把 COM 口或 camera index 当作永久身份 | 已实现/架构约束 | 串口 stableId 仅在 pyserial 提供 VID/PID/serial_number 时生成；COM 只保存为 `lastPort`。RGB 会尝试读取 Windows FriendlyName/PnP/VID/PID/USB serial，但 DirectShow index 或仅按顺序推断的映射只保存为 `lastDeviceIndex`/`mappingConfidence=inferred`，不生成 stableId。DVP2 优先按 serial/user id 匹配 |
| 设备发现阶段不得触发机械动作 | 已实现/测试覆盖 | 串口 discovery 对候选 COM 只执行 open/PING/close；已连接的正式 `SerialService` 端口标记 `inUse=true`，不二次打开；不调用风扇、门、光源、滤光轮或电机动作命令 |
| STM32 current firmware 协议必须隔离在上位机 adapter 内 | 已实现/SOFTWARE IMPLEMENTED，待实机验收 | P1B-7.5A.1 已在 `stm32_protocol.py` 绑定 `CURRENT_STM32_FIRMWARE_PROFILE`：AA55 frame、CRC16-CCITT-FALSE 覆盖 `CMD+PLEN+PAYLOAD`、ACK echo/result、STATUS 18 bytes、INFO 13 bytes，以及当前命令号 `MOVE_ABS=0x01`、`MOVE_REL=0x02`、`STOP=0x03`、`SET_POS_PID=0x04`、`SET_VEL_PID=0x05`、`SET_PROFILE=0x06`、`SET_CONFIG=0x07`、`QUERY_STATUS=0x08`、`SET_ORIGIN=0x09`、`RESET=0x0F`、`FAN_SET=0x10`、`DOOR_SET=0x11`、`LED_SET=0x12`。`stm32_controller.py` 负责 STATUS/INFO cache、handshake、ACK echo correlation、command serialization、status-aware MOVE_REL retry、PPR consistency diagnostic 和 safe_stop 动作报告 |
| STM32 当前电机只代表滤光片轮，不得接入 SampleStage | 已实现/架构约束 | P1B-7.5A.1 adapter 只提供 filter-wheel mapping；默认 16 slots、1600 ppr、22.5°/slot，pulses 仅诊断，主机 MOVE payload 为 float32 LE degrees + rpm；P1B-7.5B 的 SampleStage API/UI 仍走独立 adapter 边界，默认 `SAMPLE_STAGE_PROTOCOL_UNKNOWN`，禁止复用当前 STM32 filter-wheel motor |
| 当前 firmware 不支持的硬件能力不得伪造成成功 | 已实现/架构约束 | 非零 tungsten 控制在 current-firmware adapter 下会明确为 capability unavailable；FAN_SET payload 为 duty 0/100；LED_SET payload 为 bitmask 0x00..0x07 且 LED3 可表达；DOOR_SET payload 为 0 raise/retract、1 close/extend、2 stop，ACK 只代表 command accepted，不代表 endpoint reached；SET_ORIGIN 只表示人工对准后建立逻辑 0°，不表示 automatic HOME sensor；STATUS position 只作为 logical position，不标记 CL57C physical encoder verified；PPR mismatch 只报告并要求人工确认，不自动 SET_CONFIG |
| STM32 Web 硬件调试必须通过后端统一控制链 | 已实现/需实机验收 | 设备准备/电机页已提供风扇 on/off、LED3 on/off、推杆伸出/缩回/停止、滤光轮顺/逆时针相对移动、滤光轮 STOP、人工 SET_ORIGIN。所有 HTTP API 通过 `DeviceManager -> Stm32ControllerAdapter -> SerialService`，不新建串口服务，不调用 `manual_stm32_test.py`，不在前端映射协议 payload |
| STM32 运动与输出验证必须使用 fresh STATUS | 已实现/需实机验收 | STATUS cache 带 revision 和 `statusReceivedMonotonic`；`query_status(require_fresh=True)` 不用旧缓存冒充新状态。风扇/LED3 命令后回读 fresh STATUS 验证 duty/mask；Web 滤光轮移动默认 `maxRetries=0`，只有 post-command fresh STATUS 证明 target/position 变化、最终位置到达、motor idle/done 且 errorCode=0 才算完成。ACK OK 但 target/position 未变化失败为 `motion_not_started` 并 STOP |
| 推杆控制不得标记端点到位 | 已实现/需实机验收 | UI 使用“推杆伸出/推杆缩回/推杆停止”，后端映射 DOOR_SET 0x01/0x00/0x02；ACK 仅表示 command accepted。伸出/缩回使用 100..5000ms 后端 timer 自动 STOP，并用 token/generation 避免旧 timer 停掉新动作；无 door endpoint feedback 时不得显示 motion completed |
| 当前 firmware 无远程 Fault Clear | 已实现 | `fault_clear` 在 current firmware 下返回 unsupported capability；UI 禁用“清除故障”并提示现场断电/复位后重新连接 |
| 相机服务层应与样品保存目录解耦 | 已实现 | `camera_service` adapter 返回 numpy 帧和状态，不决定 Sample Folder、文件名或 `rgbDirName/multispectralDirName` |
| RGB 相机帧色彩格式必须明确 | 已实现 | `RgbUvcCamera.capture_frame()` 把 OpenCV BGR 转为 RGB，返回 RGB `uint8` H×W×3 |
| RGB 相机状态必须区分检测、可用、打开、预览 | 已实现 | `CameraStatus` 暴露 `detected/available/opened/streaming`；probe 成功后释放句柄或停止预览不清空 `detected/available`；重新检测只使用当前配置的 device index，可兼容同 index 的两种 DirectShow 打开形式，不自动 fallback 到内置摄像头 |
| RGB 相机设置应区分保存配置和应用到真实相机 | 已实现 | 相机设置页提供“应用到相机”“应用并保存”“保存为默认配置”“恢复已保存配置”“恢复默认配置”；`/api/camera/rgb/apply-settings` 下发参数并回读 actual 状态，`persist=true` 时成功后写入后端 `config/camera_settings.json`；后端保存 device identity/index、width/height/fps/fourcc、auto/manual exposure、gain 和 white balance，并记录 requested/actual/settingResults |
| 相机参数应有后端权威持久化与启动恢复闭环 | 已实现/SOFTWARE IMPLEMENTED | `CameraSettingsStore` 负责 JSON UTF-8 load/save/reset/migrate，缺文件返回 default，损坏文件返回 warning/default，写入使用 atomic replace。`CameraManager` 在 probe、preview/capture open、显式 restore 和 reconnect 时从后端保存的 requested settings 重新下发并回读；`get_status` 不重复 set 参数；restore state 为 `not_attempted/restored/partial/failed/device_mismatch`。旧 `fruitAnalyzer.cameraSettings` 只作为 UI cache/legacy migration，不再是 authoritative source |
| RGB 预览应与正式采集分离 | 已实现/部分真实 | `/api/camera/rgb/preview/start` 使用同一个 `RgbUvcCamera` 实例启动后台 latest-frame/latest encoded JPEG worker，`/api/camera/rgb/preview-frame` 直接返回最新 960x540 JPEG 预览缓存；`CameraManager.capture_rgb_frame()` 通过同一个 RGB adapter 取正式 RGB 帧，不使用预览 JPEG，不在预览运行时打开第二个 UVC 句柄；响应和 UI 显示 frameId、source age、capture/resize/JPEG/server/browser fetch 耗时、measured FPS、drop 计数和 encoder；P1B-8 True Capture 入口也只读取正式 `CameraFrame` |
| 正式 scientific capture 文件必须使用 lossless PNG | 已实现/测试覆盖 | 受保护 RGB 单帧、DVP2 raw mono 单帧、多波段 sample sequence、Dark/White reference、Sample MultiView 和 `create_offline_capture_dataset()` 离线验证正式数据均保存 `.png`；正式 metadata 不得引用 `.jpg/.jpeg`；JPEG 只允许用于 `/api/camera/*/preview-frame` 浏览器预览 |
| RGB 正式 scientific capture 的 PNG 前链路必须 strict lossless | 已实现/当前硬件 BLOCKED | P1B-8.1 起正式 RGB capture 必须验证 actual source transport；`MJPG/MJPEG/JPEG/H264/H265` 为 lossy，`YUY2/YUYV/UYVY` 记为 `uncompressed_but_chroma_subsampled` 但 strict lossless=false，只有 RAW Bayer/RGB24/BGR24 或真实确认的完整未压缩 pixel transport 才允许保存 PNG。当前环境 `manual_camera_test.py --rgb-lossless-probe` 对 `device_index=1` DirectShow 打开失败，未找到 strict-lossless mode，因此 True Capture readiness 会 BLOCK RGB scientific capture |
| 多光谱相机接口必须支持未来 16-bit mono | 已实现/部分实机 | `CameraFrame` 不强制 `uint8`，允许 `uint16` H×W 单通道；DVP2 binding 的 `frame_to_array()` 会按 `dvpFrame.bits` 保留 `uint8` 或 `uint16`，预览 JPEG 才做显示归一化；用户当前实机验证为 `Mono8/uint8`，代码仍保留 `uint16` 边界 |
| 多光谱相机网页预览应读取真实 DVP2 当前流 | 已实现/需现场复核 | `/api/camera/multispectral/preview/start` 打开并保持同一个 DVP2 实例和 stream，后台 worker 持续读取当前流并生成最新 960x540 JPEG 预览缓存；预览响应包含 source dtype、PixelFormat 和亮度 min/max/mean |
| 多光谱网页预览应优先低延迟显示最新帧 | 已实现/需现场复核 | P1B-5.4 后 preview 使用后台 latest-frame cache，本轮进一步把 resize/JPEG 编码移入 worker 并维护 latest encoded JPEG cache；HTTP 请求不再排队调用 DVP2 `get_frame()` 或逐请求编码，只返回最新 JPEG cache；允许丢弃旧帧，响应和 UI 显示 frameId、sourceTimestamp/source age、capture/resize/JPEG/server/browser fetch 耗时、measured FPS、drop 计数和 encoder；默认目标 12 FPS，前端限制最多一个 in-flight request |
| 多光谱曝光/增益应能从网页真实下发并回读 | 已实现/需现场复核 | `/api/camera/multispectral/apply-settings` 调用 `Dvp2MonoCamera.set_exposure()` 和 `set_gain()`，set 后由 adapter 执行真实 get readback；曝光单位为 μs；范围来自 SDK capability，不在前端硬编码。P1B-8.1 后支持 `persist=true` 保存 DVP2 device identity、exposure 和 gain，并在启动/重连/显式 restore 时重新 set + get；不开放 PixelFormat、trigger、ROI 持久化 |
| 多光谱 PixelFormat 本阶段只显示不切换 | 已实现 | 当前实际 `pixelFormat/frameDtype` 与 `supportedPixelFormats` 分开；只验证 `Mono8`，不开放格式切换 UI |
| DVP2 正式单帧保存必须保留 raw mono 位深 | 已实现/需实机复核 | `CameraManager.capture_multispectral_frame()` 返回 DVP2 `CameraFrame.data`，`CaptureCoordinator.run_multispectral_capture()` 只接受二维 MONO `uint8/uint16`，保存 PNG 前后读回验证尺寸、单通道和 dtype；不使用 960x540 预览 JPEG，不做 `astype(uint8)`、`/256` 或显示归一化 |
| DVP2 low-latency preview 不得影响 scientific capture | 已实现 | latest-frame/latest encoded JPEG cache 仅用于 `/api/camera/multispectral/preview-frame`；P1B-4 单帧和 P1B-5 多波段 sequence 仍通过 `capture_multispectral_frame()` 直接获取 raw `CameraFrame`，不读取 preview JPEG，不用 cache 冒充正式采集，不改变 uint8/uint16 raw PNG 保存和滤光轮同步 |
| 正式采集 metadata 应记录相机参数来源与恢复状态 | 已实现 | `CameraManager.capture_rgb_frame()` 和 `capture_multispectral_frame()` metadata 记录 `requestedSettings`、`actualSettings`、`settingsSource` 和 `settingsRestoreState`；True Capture readiness 对 saved device mismatch 返回 `CAMERA_SETTINGS_DEVICE_MISMATCH` blocker，对 restore failed 返回 warning；没有保存自定义参数时 `settingsSource=default` 不阻断采集 |
| DVP2 单帧未同步滤光轮时不得伪造波长 | 已实现 | P1B-4 metadata 固定记录 `wavelengthNm=null`、`bandAssignment=unassigned`、`filterWheelSynchronized=false`，`bands` 仍为空，不写假滤光轮位置 |
| DVP2 多波段 sequence 必须由滤光轮确认后采集 | 已实现/需实机验收 | P1B-5 `CaptureCoordinator.run_multispectral_sequence()` 会先 HOME，再按 enabled band 读取目标轮位、相对移动、查询确认位置、等待稳定、应用该 band 的 exposure/gain，之后才保存 DVP2 raw PNG；位置未知或不匹配会失败并 `safe_stop()`，不保存未同步 band |
| 多波段 metadata 必须记录真实 band plan 和部分完成状态 | 已实现 | P1B-5 metadata 写入 `bands`、`multispectralSequence`、enabled/disabled/completed/pending/failed bands、filter config source/version、developmentConfig、settlingMs、`partialCapture`、`cancelled` 和每帧 `wavelengthNm`/`bandAssignment`/`filterWheelSynchronized=true` |
| Dark/White reference 必须是 raw scientific 数据 | 已实现/需实机验收 | P1B-6 Dark/White reference 使用同一 DVP2 raw mono scientific saver，保存 `calibration/dark/band_XX_<bandId>.png` 与 `calibration/white/band_XX_<bandId>.png`，不使用 preview JPEG、不归一化、不把 `uint16` 转 `uint8`；每帧 metadata 明确 `captureType`、band、wavelength、filterWheel、requested/actual exposure/gain、min/max/mean/std |
| CalibrationSet 必须可追溯并可做兼容性检查 | 已实现 | P1B-6 写入 `calibration/calibration_set_<calibrationId>.json`，记录 camera identity、filter config、bands、dark/white frames、completed/missing bands、`calibrationComplete`、`sameBandSettingsMatched`；`validate_calibration_compatibility()` 比较 camera、filter config、band/wavelength、尺寸、dtype、PixelFormat、exposure/gain，并区分 compatible/warning/incompatible |
| 多视角样品采集必须按 Sample -> View -> RGB + multispectral sequence 编排 | 已实现/SOFTWARE IMPLEMENTED | P1B-7 新增 `SampleViewPlan` / `SampleMultiViewPlan` 和 `run_sample_multiview_capture()`；每个 View 先样品台移动、位置确认和 settling，再采 RGB，然后复用 P1B-5 多波段 sequence，最终记录 `viewComplete`、`completedViews`、`failedView`、`pendingViews` 和 `multiViewCaptureComplete` |
| 多视角采集必须保持同一个 sample_id 并复用同一个 CalibrationSet | 已实现 | P1B-7 metadata 中所有 View 保持相同 `sample_id`，并通过 sample 级 `calibrationId` 引用一次 Dark/White `CalibrationSet`；不会把每个 View 当独立样品，也不会每 View 重拍 Dark/White |
| 样品台硬件模式不得伪造成功 | 已实现/协议未知 | P1B-7.5B 扩展 `sample_stage.py` 状态模型和 DeviceManager/API/UI 调试边界；默认硬件状态明确为 `SAMPLE_STAGE_PROTOCOL_UNKNOWN`，`protocolKnown=false`、`positionFeedbackSupported=false`，HOME/move/STOP 返回 unsupported。simulation 只用于 unittest/离线软件编排验证，不会自动 fallback 为硬件成功 |
| 支持创建样品并保存元数据 | 已实现 | `/api/new-sample` 写 `metadata.json` |
| 每次样品创建应生成唯一保存目录 | 已实现 | `create_unique_sample_folder()` |
| 本次拍摄目录自动进入分析流程 | 部分实现/模拟 | 离线采集会设置 `analysisDataDir` |
| 用户可手动选择其他样品目录分析 | 已实现 | 主 UI 支持当前/其他文件夹 |
| 样品拍摄保存前应允许设置 RGB 与多光谱图像子目录名称 | 已实现 | 用户选择保存父目录后弹出“图像目录名称设置”；默认 `rgb`/`multispectral`，校验空值、非法字符、路径分隔符、`.`/`..` 和重名 |
| 手动读取样品时应先选择父文件夹，再选择 RGB/多光谱一级子目录 | 已实现 | `/api/inspect-image-folders` 扫描父目录直接子目录并建议角色；确认后 `/api/sample-folder` 使用实际选择的 `colorDir/multispectralDirName`；旧 `depthDir` 仅作为历史兼容 alias |
| 文件夹/文件路径选择应使用系统原生选择器，普通用户不需要手动输入完整 Windows 路径 | 已实现 | 主程序保存位置、其他样品文件夹、Model Studio 导入来源/样品文件夹和 labels.csv 均为只读路径显示 + 选择按钮；取消选择保留原路径 |
| 样品目录应包含 RGB、多光谱、校准目录 | 已实现/部分实现 | RGB/多光谱子目录名可配置并写入 `metadata.json.image_directories`；默认继续兼容 `rgb`/`multispectral`；P1B-3 受保护 RGB 单帧保存会写入 `<rgbDirName>/rgb_view_000.png`；P1B-4 受保护 DVP2 单帧保存会写入 `<multispectralDirName>/multispectral_frame_000.png`；P1B-5 受保护多波段 sequence 会写入 `<multispectralDirName>/band_XX_<bandId>.png`；P1B-6 受保护 Dark/White reference 会写入 `calibration/dark/band_XX_<bandId>.png`、`calibration/white/band_XX_<bandId>.png` 和 `calibration/calibration_set_<calibrationId>.json`；P1B-7 受保护 MultiView 会写入 `views/<viewId>/rgb/rgb_<viewId>.png` 和 `views/<viewId>/multispectral/band_XX_<bandId>.png`，同时保留旧单层目录兼容 |
| 样品采集应支持样品台多角度旋转拍摄设置 | 已实现/硬件阻塞 | 主程序可设置期望角度间隔、起始角度、方向和闭合补拍；后端生成 `captureRotationPlan` 并写入 metadata；P1B-7 复用该 plan 做 MultiView 软件编排；P1B-7.5B 新增样品台状态/API/UI 调试边界，但真实样品台控制器协议未知，硬件动作仍不可用 |
| 样品旋转角度必须与滤光片转轮角度分开 | 已实现 | `sample_rotation` 用于样品台多视角，`filter_wheel_rotation` 用于多光谱波段切换；metadata 明确两者独立 |
| 多角度采集默认不得重复拍摄 360° | 已实现 | `views=ceil(360/interval)` 后重新均分一周，默认不生成闭合 View；只有启用“补拍闭合角度”才保存 closure view |
| 检查数据完整性时按启用波段判断，不要求 RGB 与多光谱数量相等 | 已实现 | 测试覆盖 |
| 本地已有样品目录应可直接做形态分析，不强制新建当前样品 | 已实现 | `/api/analyze-shape` 不再要求当前样品；用户选择有效样品文件夹后可直接分析 |
| 当前形态分析以 RGB/多光谱二维测算为主 | 已实现/部分实现 | 输出面积、宽高、颜色、纹理、波段均值 |
| 主程序中央区应区分采集模式和分析模式布局 | 已实现 | `motor/light/camera/capture/settings` 保留 RGB + 多光谱相机面板；`shape/sugar/acid/taste` 隐藏相机面板并让分析内容占用中央空间 |
| 三维点云建模作为后续预留 | 部分实现 | 兼容旧 RGB-D/PLY；主 UI 禁用点云模式 |
| SSC、TA、pH 应分别预测 | 部分实现 | 入口和模型加载已实现；当前无生产模型 |
| 预测结果不得伪造 | 已实现 | 无模型时返回 `model_missing`，测试覆盖 |
| 糖酸比和口感等级由 SSC/TA 推导 | 部分实现 | 前端实现简单规则 |
| 模型训练支持 PLSR、SVR、Random Forest | 已实现 | `training/train.py` |
| 预处理支持 RAW、SNV、MSC | 已实现 | `quality_algorithm/preprocessing.py` |
| 模型训练数据不足时应失败 | 已实现 | `InsufficientTrainingDataset` |
| Model Studio 管理数据集、标签、特征、训练、候选模型和发布 | 已实现/部分实现 | 代码完整；当前无真实数据 |
| Dataset 应支持后续继续添加样品并创建新版本 | 已实现 | `import_samples()` 可对已有 Dataset 执行 Add Samples；重复样品支持 Skip、Replace Working Copy、Create New Sample ID、Cancel，其中 Replace 会在样品已被历史 Version/Experiment/Model 引用时拒绝，避免破坏旧快照 |
| Model Studio Dataset 应使用本地托管仓库，不直接依赖外部原始目录训练 | 已实现 | 样品导入会 COPY 到 `model_studio_data/datasets/<dataset_id>/samples/`，训练读取本地副本 |
| Model Studio 导入样品前应验证 RGB、多光谱、校准和 metadata | 已实现 | `validate_sample_folder()` 返回 Valid/Warning/Invalid，复用当前目录完整性规则并补充 `metadata.json` 检查 |
| Model Studio 重复导入样品不得静默覆盖 | 已实现 | 默认跳过重复样品，可选择作为新样品导入 |
| Model Studio 样品标签 SSC/TA/pH 应显式保存 | 已实现 | 标签输入是前端未保存状态，点击“保存标签”后才写 SQLite |
| 标签保存后同步维护 Dataset 本地 `labels.csv` | 已实现 | `save_sample_label()` 和 `import_labels()` 都会重写本地 `labels.csv` |
| Model Studio 数据准备页面应按 Workflow 展示操作顺序，避免按钮墙 | 已实现 | Dataset 页面按创建数据集、导入样品、标签录入、数据质量、创建版本组织；样品页以样品列表和选中样品 Ground Truth 为主 |
| 允许部分标签参与对应目标训练 | 已实现 | 空 SSC/TA/pH 保存为 NULL，样品标签状态显示 Missing/Partial/Complete |
| Dataset Version 应冻结样品和标签快照 | 已实现 | `sample_snapshot_json`/`label_snapshot_json` 记录版本创建时的本地路径和标签值 |
| 删除 Sample 时不得影响原始拍摄目录 | 已实现 | 可删除数据库记录，或二次确认后删除本地托管副本；不删除 `source_path` |
| 噪声 Sample 应优先 Exclude 而不是 Delete | 已实现 | `include_status` 支持 Included / Needs Review / Excluded；Exclude reason 支持 Image Blur、Missing Band、Calibration Error、Label Error、Damaged Fruit、Outlier、Capture Error、Manual Exclusion、Other；Excluded 不进入新的 Dataset Version，但历史 Version 保持可复现 |
| 被历史 Version / Experiment / Model 引用的 Sample 禁止永久删除 | 已实现 | `sample_references()` 会显示引用关系，`delete_sample()` 和 duplicate Replace 会拒绝破坏已有 lineage 的操作 |
| Dataset Version 应支持 Diff | 已实现 | `dataset_version_diff()` 比较两个不可变快照，输出 added samples、removed/excluded samples 和 SSC/TA/pH label changes |
| Production/Default 模型必须人工发布 | 已实现 | `publish_model()` / `set_default_model()` |
| 系统不能自动替换正式模型 | 已实现 | 候选与发布目录隔离 |
| Model lifecycle 应区分 Candidate / Validated / Published / Archived / Default | 已实现 | `Default` 继续兼容为 Published 模型的自动选择角色；Archived 保留记录和文件但不进入主工作台模型目录 |
| Model Permanent Delete 必须同步清理 DB 和文件且保护 Default | 已实现 | `/api/model-studio/models/delete` 需要 model_id 确认；Default 和仍对应 legacy default bundle 的模型禁止直接删除；非 Default 删除会移除 SQLite 记录和受管 candidate/published artifacts；Model Card 的“更多”菜单提供状态化入口 |
| Retrain 不得覆盖旧模型 | 已实现 | `retrain_from_model()` 带出 `parent_model_id` 创建新 Experiment/Run/Model，旧模型保留，由人工决定 Publish / Set Default |
| 支持不同水果/品种使用不同模型 | 已实现 | 模型目录和 SQLite 按 fruit_type/variety 过滤 |
| 支持 generic 品种模型兜底 | 已实现 | `model_catalog()` 和 `_select_registry_model()` |
| Published 非 Default 模型应可被主工作台手动选择 | 已实现 | `/api/quality-models` 返回 Published / Default / Production；Default 只负责自动选择，Archived 不可见 |
| 普通检测用户不应默认看到 model_id、PLSR/SVR/RF、RAW/SNV/MSC 等高级模型细节 | 已实现 | 采集页新增“检测模型”摘要，默认显示 SSC/TA/pH 模型配置状态；点击“更换模型”后才展开原有模型下拉框 |
| 已选择模型但尚未预测时不应显示“未接入” | 已实现 | 主工作台加载兼容模型后立即显示真实 display_name、version、algorithm、preprocessing，并标记“已选择，等待预测” |
| 本地样品目录缺少 fruit_type / variety metadata 时必须提示 Sample Scope | 已实现 | `/api/sample-folder` 对有效但缺 scope 的目录返回 `requiresSampleScope` 和可选 fruit/variety 来源；用户选择后写入当前会话并重新加载兼容模型，不做中英文 alias 猜测 |
| 保留当前 UI，不为新功能推倒重做 | 已确认 | 本次上下文整理明确为设计约束 |
| 后续重要架构/需求/功能修改要同步文档 | 已确认 | 见 `AGENTS.md` |

## 待确认需求

| 需求 | 当前问题 |
| --- | --- |
| 第一批正式检测水果/品种 | 文档倾向蓝莓/Duke，但代码允许任意 fruit_type/variety |
| 正式滤光片数量与波段 | 历史文档提 6-16 孔，当前开发配置只启用 450/560/670 nm |
| TA 单位与理化实验协议 | 当前 UI 显示 `%`，需确认实验室标签单位 |
| pH/TA/SSC 最低样本量与验证标准 | 代码最低训练样本门槛较低，真实上线门槛待定 |
| 模型发布审批标准 | 可手动发布，但缺少必须满足的 R2/RMSE/RPD 阈值 |
| 报告格式 | 当前为 TXT，是否需要 PDF/Excel/数据库记录待确认 |
| 历史记录结构 | 历史文档有建议表，当前检测工作站未实现 |
| Dataset 删除/归档策略 | 已实现 | Archive 保留 Dataset/Samples/Versions/Experiments/Models/Lineage 并默认隐藏；Permanent Delete 需要 Dataset Name 确认，Published/Default/Production 模型引用会阻止删除，Candidate/Validated/Archived 实验模型可随 Dataset 级联清理；仅删除 Model Studio 受管目录，不删除外部 source_path |
| 硬件通信协议最终格式 | 当前主路径保留 zdyzzddy 两字节协议 `[CMD][PARAM] -> [CMD|0x80][RESULT]` 兼容；P1B-7.5A.1 后默认生产 adapter 已使用当前 AA55 firmware profile，旧 short-frame 只作为兼容边界保留。P1B-7.5B 搜索仓库后未找到独立样品旋转台协议，需补齐控制器资料后才能实现 RealSampleStageAdapter。真实硬件 smoke 后再把现场验证状态写入文档 |
| RGB/DVP2 网页预览现场复核 | RGB 本轮已在当前电脑完成 CLI benchmark，但仍需在主程序相机设置页复核浏览器端 sourceAge、server、fetch、FPS 和 drop；用户已确认 DVP2 完全退出 BasedCam3 后 manual test 可打开和取帧，本轮 Codex 复测时当前运行环境 DVP2 枚举返回 0，需要在设备在线时复核重新检测、打开预览、曝光/增益应用、停止/重启预览 |
| P1B hardware acceptance 实测记录 | 已建立规范，待执行 | `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md` 已列出 RGB、DVP2、STM32、Fan、LED3、Door/Actuator、Filter Wheel、Dark/White、Multi-band、Sample Stage、Multi-view、Scientific Data、Metadata、Safety 和 True Capture gate 的执行步骤与 PASS/FAIL 标准；下一步需要现场填写证据 |
| STM32 设备身份命令 | 当前固件协议只有 PING 等两字节命令；PING 只能证明兼容协议，不能区分 MAIN_CONTROLLER 或 ROTATION_CONTROLLER。下一版建议增加 GET_DEVICE_TYPE / GET_DEVICE_INFO，返回 deviceType、deviceId、firmwareVersion 和 capabilities |
| 是否保留网页局域网访问 | 当前启动本地 127.0.0.1；远程访问和权限待定 |

## 未实现但重要的需求

| 需求 | 备注 |
| --- | --- |
| 真实黑白相机多波段采集 | DO3THINK/度申 GigE/RJ45 黑白相机已完成 DVP2 adapter、manual test 用户实机通过、网页预览/参数控制 API、raw mono 单帧保存、滤光轮+DVP2 多波段 sample sequence、Dark/White calibration sequence 和 P1B-8 单视角 `/api/capture/start` 软件入口；下一步需要真实滤光轮现场验收、暗白物理校正验收、样品台真实控制器和多视角硬件联调；不能用 Wi-Fi link 或 OpenCV VideoCapture 替代 |
| STM32 串口通信 | 已有两字节协议、超时、状态查询和测试；仍需实机验证和采集编排 |
| 滤光轮 HOME/GOTO/报警 | 已有 HOME、相对旋转、位置查询、故障码查询；P1B-5 软件 sequence 已用 HOME/相对旋转/位置查询完成 band 同步；绝对 GOTO、最终 STM32 contract 和真实硬件验收待补 |
| LED 光源开关和亮度控制 | 已有 RGB LED/钨灯开关命令；亮度控制、三路 LED 是否需要扩展需硬件确认 |
| 门控、急停、温度、电机报警状态 | 升降门、急停、故障码已有基础 API；温度和扩展报警未接入 |
| RGB 与多光谱标定配准 | `calibrated` registration 未实现 |
| 正式 Production 模型 | 当前仓库没有模型文件和真实训练数据 |
| 检测历史记录数据库 | 当前只有 Model Studio SQLite，不保存检测结果历史 |
| 正式报告导出 | 当前前端导出 TXT |
