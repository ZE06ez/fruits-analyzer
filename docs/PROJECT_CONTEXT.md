# Project Context

更新时间：2026-10-03（北京时间）

本次状态核对基线：`main@9e23790fd2260b237df1560727cd342b2d6376bc`。当前简要状态见 [PROJECT_STATUS.md](../PROJECT_STATUS.md)。此次只核对仓库代码/配置/测试与文档，未实机测试、未读取用户本机运行时数据库，未重新执行完整回归。历史实机记录与正式验收结果须分开。

本文档记录当前项目的真实上下文。判断优先级固定为：当前真实代码 > 当前配置/数据库结构 > 当前测试 > 最新项目文档 > 历史项目文档 > 历史聊天上下文。若历史描述与代码冲突，以代码为准。

补充（2026-09-30）：主 UI 的设备入口已收拢为唯一“设备与维护”工作区。首屏按系统摘要、核心设备（STM32、RGB、DVP2）、光学系统与运动系统展示；手动硬件控制和 raw diagnostics 收进对应设备 Inspector 的 Advanced 层。该信息架构不改变 STM32/相机/capture 后端、真实硬件边界或 `ready / blocked / running` Hardware Action Gating：诊断检查可点击，blocked 动作仍只展示原因且不发 API，running 才真正 disabled。

补充（2026-09-25）：主 UI 的硬件动作使用统一 `ready / blocked / running` 状态。Blocked 不使用原生 `disabled`，点击只显示真实状态推导的前置条件说明，前端不会发出命令；Running 才使用 `disabled` 防重复。后端 interlock 和采集 readiness 仍是最终安全边界。

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
| RGB 彩色相机 | `camera/rgb` 厂商资料显示 Windows AMCAP/UVC 与锐尔威视 USB Camera SDK；用于外观和形态 | PARTIAL/REAL VERIFIED：`camera_service.RgbUvcCamera` 通过 OpenCV `cv2.CAP_DSHOW`/UVC 打开当前电脑 `device_index=1`，请求 `MJPG`、`3840x2160`、`25fps`，实机验证可取 `(2160, 3840, 3)` `uint8` RGB 帧；配置在 `RgbCameraConfig`，不在 adapter 内硬编码；同一逻辑 index 兼容 `VideoCapture(index, CAP_DSHOW)` 与 `VideoCapture(index + CAP_DSHOW)` 两种 DirectShow 打开形式，不扫描或 fallback 到 0；相机设置页“重新检测”调用 `/api/camera/rgb/probe` 执行真实打开/取帧/释放，状态区分 `detected`、`available`、`opened`、`streaming`，释放句柄或停止预览后仍保留已检测/可用状态；`/api/camera/rgb/apply-settings` 可下发并回读参数，P1B-8.1 起支持 `persist=true` 在成功回读后写入后端 `config/camera_settings.json`，并在 probe、preview/capture open、explicit restore/reconnect 时从 saved requested settings 重新下发和回读；`/api/camera/rgb/preview-*` 使用后台 latest-frame + latest encoded JPEG cache 做 960x540 低延迟预览，UI 显示 source age、capture/resize/JPEG/server/browser fetch、FPS、drop 和 encoder；P1B-3 已新增 `CaptureCoordinator.run_rgb_capture()` 单帧 RGB 正式 PNG 保存路径，P1B-8 单视角 `/api/capture/start` 会复用该正式 `CameraFrame` 路径；正式 capture 不读取预览 JPEG/cache；多视角硬件采集仍被真实样品台协议阻断 |
| 黑白多光谱相机 | DO3THINK/度申 GigE/RJ45 工业黑白相机，通过 PC 千兆以太网口和 DVP2 SDK 连接；配合滤光片转轮采集多光谱灰度图 | PARTIAL/REAL ADAPTER：`camera_service.Dvp2MonoCamera` 已基于 `D:\Netease\DVP2 SDK CN\library\Visual C++\include\DVPCamera.h` 和官方示例新增 Python 3.12 `ctypes` 绑定；可发现 `DVPCamera64.dll`，真实枚举目标为 `MGV231M-H2-169.254.25.110`、`UserID=GP23400004963`、SDK serial `DSGP23400004963`、MAC `B4-61-D3-14-6E-18`；用户已确认完全退出 BasedCam3 后 manual test 可打开、参数读取、开始取流、30 帧取图和 PNG 保存，实际帧 `2048x1200`、`Mono8`、`uint8`；相机设置页已接入 DVP2 重新检测、低延迟网页 JPEG 预览、曝光/增益下发与回读、设备详情和占用提示。P1B-8.1 起 DVP2 只持久化 device identity、exposure、gain，Apply & Save 成功后写入后端 camera settings store；恢复时必须调用 `set_exposure()`/`set_gain()` 后回读 actual，不保存或开放 PixelFormat/trigger/ROI。P1B-5.4 后多光谱 preview 使用后台 latest-frame cache，本轮进一步在 worker 中维护 latest encoded JPEG cache，HTTP 请求直接返回最新 JPEG 预览缓存，允许丢弃旧帧，并显示 frameId/source timestamp/age/阶段耗时/FPS 诊断；该 cache 不作为正式采集输入。P1B-4 已新增 DVP2 raw mono 单帧正式 PNG 保存；P1B-5 已新增 `MultispectralBandPlan` / `MultispectralCapturePlan` 与 `CaptureCoordinator.run_multispectral_sequence()`，可按 filter config 或显式 band plan 对 enabled bands 执行滤光轮 HOME、相对移动、位置确认、稳定等待、按波段曝光/增益应用和 DVP2 raw PNG 保存，metadata 记录真实波段、波长和 `filterWheelSynchronized=true`。P1B-6 已新增 `run_dark_reference_capture()` 与 `run_white_reference_capture()` 软件路径，复用同一 band plan/滤光轮/相机设置/raw PNG saver，保存 `calibration/dark/` 和 `calibration/white/`，生成 `CalibrationSet` 和 `calibrationId`，记录 saturation、dark leakage、uniformity 与 compatibility metadata。暗白物理校正验收、样品旋转和完整 `/api/capture/start` 闭环仍未接入，真实滤光轮硬件验收仍待完成 |
| 滤光片转轮 | 文档建议 8-16 孔，STM32 控制 HOME/定位；代码开发配置启用 450/560/670 nm | PARTIAL：已有 `serial_service.py`、`hardware_controller.py`、`device_manager.py` 的两字节 STM32 串口控制层和滤光轮寻零/相对旋转命令；P1B-5 的多波段 sequence 只通过 `HardwareController.wheel_home()`、`wheel_move_relative()`、`get_wheel_status()` 高层 API 使用滤光轮，不直接发送串口命令；P1B-7.5A.1 已绑定当前 STM32 AA55 firmware profile，P1B-7.5A.2 新增 Web 控制和 fresh STATUS 运动验证。STATUS cache 带 revision 与 `statusReceivedMonotonic`，Web 手动 MOVE_REL 默认不 retry，ACK OK 但 target/position 未变化会失败为 `motion_not_started` 并 STOP；支持 CRC16-CCITT-FALSE、stream parser、STATUS/INFO cache、ACK echo correlation、command serialization、16孔/1600ppr/22.5° slot mapping、float32 LE degrees+rpm MOVE payload、FAN duty 0/100、LED3 mask 0x04/0x00 和 DOOR accepted-only command。PPR mismatch 只报告并要求人工确认，不自动 SET_CONFIG；SET_ORIGIN 只表示人工对准建立逻辑 0°，不是自动 HOME sensor；当前 firmware 不支持远程 Fault Clear；真实滤光轮现场验收仍未完成 |
| 样品旋转平台 | 围绕水果旋转，用于 RGB + 多光谱多视角采集 | BLOCKED/SOFTWARE BOUNDARY：主程序已支持 `sample_rotation` 角度计划、UI 设置、metadata/views.json 记录和离线模拟多 View 文件；P1B-7 新增 `sample_stage.py` 高层边界与 `CaptureCoordinator.run_sample_multiview_capture()`，可按 View 编排样品台位置确认、RGB 正式帧和 DVP2 多波段 sequence；P1B-7.5B 扩展 `SampleStageStatus`、connect/home/move_to/move_relative/stop/get_status/get_position/safe_stop 边界，并新增 `DeviceManager`/后端/API/UI 调试入口。仓库搜索未找到独立样品台控制板、COM/baudrate、命令格式、HOME 传感器或 position feedback 资料，现有资料明确独立样品旋转台没有真实 STM32 控制接口，因此真实硬件 adapter 当前状态为 `SAMPLE_STAGE_PROTOCOL_UNKNOWN`，AC-STAGE 继续 BLOCKED |
| 光源 | 顶部/侧向/底部/紫外/近红外光源，可调亮度并与曝光同步 | PARTIAL/REAL VERIFIED：2026-09-11 实机确认 PB7=原 LED1/bit0/0x01=钨灯1 SSR，PB8=原 LED2/bit1/0x02=钨灯2 SSR，PB9=LED3/bit2/0x04，三路统一复用当前 firmware `LED_SET=0x12` 和 STATUS `led_mask/led1_duty/led2_duty/led3_duty`，不使用独立 0x13 钨灯协议。主 UI “光源与滤光轮”已新增两路钨灯 1-5 秒限时手动测试、立即关闭和全部钨灯关闭；后端通过 `DeviceManager -> Stm32ControllerAdapter -> SerialService` 做 read-modify-write、ACK + fresh STATUS 验证、后端 auto-off、风扇/故障/LED3/人工确认 interlock。`allowDualTungsten=false`，双路同时开启和 True Capture 默认双钨灯会被阻断；RGB 正式照明映射仍未从仓库确认，默认 True Capture 旧 `rgbLedMask=0x03` 会被 `RGB_LIGHT_MAPPING_NOT_CONFIRMED` 阻断 |
| 电机/驱动器 | STM32 + 步进驱动器控制滤光轮、可能的样品台/门控 | PARTIAL：当前 STM32 motor 只代表滤光片轮，不代表水果样品台；滤光片轮和升降门已有 STM32 控制命令/adapter 边界；STATUS position 是 STM32 pulse 逻辑位置，不等于 CL57C absolute encoder feedback，`physicalEncoderVerified=false`。样品旋转平台已有 P1B-7 软件抽象和 fake 验证，但没有真实电机接口，硬件模式必须明确 `hardware_not_implemented` |
| 暗箱/样品台 | 封闭光环境，固定样品位置 | TODO：代码只体现需求和文档，没有传感器/门控/限位状态接入 |
| 真实设备状态读取 | 门状态、急停、电机报警、温度等 | PARTIAL：`/api/status` 返回 `device` 状态，`/api/device/status` 可查询风扇、门、滤光轮、光源、故障码和急停状态；温度等扩展状态未接入 |

## 3. 软件架构

当前真实软件不是 Vue/FastAPI/Electron，而是：

P1C-1 后 RGB preview profile 与 RGB scientific capture profile 是两个独立后端配置。默认 Preview 为 `3840x2160 @25fps MJPG`，只服务浏览器实时预览；默认 Scientific 为 `1920x1080 @5fps YUY2`，专用于 CaptureCoordinator 正式 RGB PNG、RGB dataset、morphology、ROI 和后续 RGB ↔ multispectral registration。正式 RGB scientific capture 不再允许 `MJPG -> OpenCV decode -> RGB -> PNG`；PNG 输出仍是必须条件，但不再是充分条件。`MJPG/MJPEG/JPEG/H264/H265` 判定为 lossy 且 `scientificCaptureApproved=false`，`YUY2/YUYV/UYVY` 记录为 `sourceCompression=uncompressed_but_chroma_subsampled`、`chromaSubsampling=4:2:2`、`scientificStrictLossless=false`、`scientificCaptureApproved=true`、`scientificQualityClass=uncompressed_422`，RAW Bayer/RGB24/BGR24 等完整未压缩 transport 为 strict lossless。缺少 `rgb.scientificProfile` 时正式采集明确失败为 `RGB_SCIENTIFIC_PROFILE_NOT_CONFIGURED`，不得静默继承 preview MJPG。

- 前端：静态 `index.html` + `styles.css` + `app.js`。
- 后端：`backend_server.py` 使用 Python 标准库 `ThreadingHTTPServer` 提供静态资源和 JSON API。
- 桌面入口：`launcher.py` 启动本地后端并打开浏览器；`FruitTasteAnalyzer.spec` 用 PyInstaller 打包，正式 exe 入口为 `launcher.py`。P1B-8.1 后 launcher 在任何 backend/browser/hardware 初始化前创建 `Local\FruitTasteAnalyzer.SingleInstance` Windows Named Mutex；后续实例只提示“FruitTasteAnalyzer 已经在运行。”并退出，不再打开已有浏览器窗口。
- 模型训练中心：`model_studio/`，同一个后端下的 `/model-studio` 静态页面和 `/api/model-studio/*` API。
- 算法：`pointcloud_service.py`、`pipeline_v2.py`、`quality_algorithm/`、`training/`、`quality_prediction.py`。

主数据流：

```text
UI
  -> backend_server.py / SessionState / JobStore
  -> 样品目录 Data/<timestamp>_<sample_name> 或用户选择目录
  -> RGB 子目录 + 多光谱子目录 + calibration/dark + calibration/white + metadata.json
  -> pointcloud_service.py 形态/表面分析
  -> quality_algorithm.spectral_features 提取多光谱特征
  -> quality_prediction.predict_ssc/predict_ta/predict_ph
  -> PredictionResult
  -> app.js 更新结果栏、糖酸比和报告文本
```

关键文件：

- `host_software/static_ui_prototype_bin/index.html`：主检测工作站 UI。
- `host_software/static_ui_prototype_bin/app.js`：前端状态机、全局系统状态派生、Operator Workbench 总览派生、采集/分析布局切换、样品流程、模型普通/高级展示、分析调用、结果渲染。
- `docs/UI_ARCHITECTURE.md`：主检测软件 Operator/Engineer 工作区边界、一级导航和 Progressive Disclosure 约束。
- `docs/UI_DESIGN_SYSTEM.md`：主检测软件 Dark Slate + Purple/Fuchsia/Cyan 视觉系统和控件状态契约。
- `host_software/static_ui_prototype_bin/backend_server.py`：HTTP API、样品会话、目录选择、离线采集、形态任务、预测接口、Model Studio API 转发，以及 `BackgroundReferenceStore` 管理 `runtime/background_reference/` 下的托管背景图、图库 JSON、active background 和样品 metadata 关联。
- `host_software/static_ui_prototype_bin/serial_service.py`：STM32F407 串口服务，115200 8N1；保留旧两字节兼容 `send_command()`，并提供 raw `write_bytes()`/`read_bytes()` 给 current-firmware protocol adapter。
- `host_software/static_ui_prototype_bin/stm32_protocol.py`：P1B-7.5A.1 AA55 current-firmware protocol 边界；包含 `CURRENT_STM32_FIRMWARE_PROFILE`、`CURRENT_FILTER_WHEEL_MAPPING`、`Stm32ProtocolProfile`、CRC16-CCITT-FALSE、AA55 codec、stream parser、STATUS/INFO decode 和 `FilterWheelMapping`。
- `host_software/static_ui_prototype_bin/stm32_controller.py`：P1B-7.5A.2 STM32 current-firmware adapter；在单一 `SerialService` owner 上处理 ACK correlation、STATUS/INFO cache revision、handshake、PPR consistency diagnostic、command serialization、fresh STATUS mechanical verification、fan/LED/door/filter-wheel action 和 best-effort safe stop。
- `host_software/static_ui_prototype_bin/hardware_controller.py`：风扇、升降门、PB9/LED3、PB7/PB8 两路钨灯、滤光片轮、状态查询、急停和故障清除控制层；光源物理映射集中为 `TUNGSTEN_1_BIT=0x01`、`TUNGSTEN_2_BIT=0x02`、`LED3_BIT=0x04`、`TUNGSTEN_MASK=0x03`、`ALL_LIGHT_MASK=0x07`。PB7/PB8 不再作为 RGB LED，钨灯复用 current firmware `LED_SET=0x12`。
- `host_software/static_ui_prototype_bin/device_discovery.py`：统一设备发现和绑定层；定义 `DeviceCandidate`/`DeviceBinding`/`DeviceRegistry`/`DeviceDiscovery`，区分 stable identity 与 current location。串口只用 open/PING/close 验证协议，不执行机械动作；缺 pyserial 时只在串口域报告 `dependency_missing`。RGB UVC 候选尝试读取 Windows FriendlyName/PnP InstanceId/VID/PID/USB serial 等信息，只有 exact/verified 映射才生成 stableId，DirectShow index 或顺序推断只作为 last known location；DVP2 优先用 serial/user id 作为稳定身份，并可在可靠匹配时显示主机 IPv4 网卡。运行时绑定保存到 `runtime/hardware_profile.json`，不把当前电脑私有 COM/index 写入源码默认配置。
- `host_software/static_ui_prototype_bin/device_manager.py`：后端设备管理层，连接串口、验证 current firmware、自检、急停与采集状态，暴露独立 SampleStage status/home/move/stop；当前 True Capture 通过动态 readiness 执行，硬件验收与生产放行仍未完成。
- `host_software/static_ui_prototype_bin/capture_coordinator.py`：P1B 采集协调器骨架，定义真实采集状态机、步骤模型、取消/超时/安全停止和 metadata 骨架；P1B-2 已接入 STM32 硬件安全准备链，可执行预检查、关门、风扇、RGB/多光谱光源准备、interlock 确认和关灯收尾；P1B-3 新增受保护的 RGB 单帧正式采集与 PNG 保存步骤；P1B-4 新增受保护的 DVP2 raw mono 单帧 PNG 保存步骤；P1B-5 新增受保护的滤光轮 + DVP2 多波段 sequence，可按 filter config/显式 band plan 保存每个 enabled band 的 raw PNG；P1B-6 新增受保护 Dark/White reference sequence、`CaptureReferenceType`、`CalibrationSet` 和 `validate_calibration_compatibility()`；P1B-7 新增 `SampleViewPlan`、`SampleMultiViewPlan` 和 `run_sample_multiview_capture()`，按样品多视角编排每个 View 的 RGB + multispectral sequence，但不放行完整 `/api/capture/start`。
- `host_software/static_ui_prototype_bin/sample_stage.py`：P1B-7/P1B-7.5B 样品台高层边界；定义 `SampleStageStatus` / `SampleStagePosition`、connect/home/move_to/move_relative/stop/get_status/get_position/safe_stop 接口语义；`UnimplementedSampleStage` 默认报告 `SAMPLE_STAGE_PROTOCOL_UNKNOWN` 且不伪造 HOME/position feedback，`SimulatedSampleStage` 只供 unittest/离线编排验证使用。
- `host_software/static_ui_prototype_bin/camera_service/`：P1A 相机服务基础层；包含统一相机异常/状态接口、RGB UVC/OpenCV DirectShow adapter、DVP2 `ctypes` binding、DVP2 黑白相机 adapter、`CameraSettingsStore`、`rgb_scientific.py` 和 `CameraManager`。P1B-8.1 后 `settings_store.py` 以 UTF-8 JSON + atomic replace 管理被 Git 忽略的运行时 `config/camera_settings.json`，缺文件时自动生成软件默认配置；`config/camera_settings.example.json` 只作为可提交参考，不绑定真实设备 serial/stableId。store 提供 load/save/get/update/reset/migrate legacy，字段白名单限制为 RGB 安全相机参数与 DVP2 exposure/gain；`CameraManager` 负责 Apply/Persist/Restore/Readback、restore state、RGB scientific transport gate（含允许的 uncompressed_422）和 capture metadata。P1B-5.4 后 `CameraManager` 负责 DVP2 preview latest-frame worker/cache 的生命周期；本轮 RGB 和 DVP2 preview 均由 latest-frame + latest encoded JPEG cache 驱动，HTTP 预览只读最新 JPEG cache；scientific capture 仍直接获取 raw `CameraFrame` 并由 Coordinator 保存 PNG。
- `host_software/static_ui_prototype_bin/rotation_plan.py`：样品台多角度旋转采集计划；与滤光片转轮角度独立。
- `host_software/static_ui_prototype_bin/pointcloud_service.py`：样品目录检查、RGB/多光谱二维形态与表面分析、兼容 RGB-D/PLY。
- `host_software/static_ui_prototype_bin/pipeline_v2.py`：旧 RGB-D/SFM 点云重建工具函数。
- `host_software/static_ui_prototype_bin/quality_prediction.py`：`SampleSession`、`PredictionResult`、SSC/TA/pH 预测入口；P1E-1 后预测端不再直接决定 legacy/identity/uncalibrated 特征参数，而是通过 shared `quality_algorithm.analysis_pipeline.run_feature_pipeline()` 生成模型输入前的 `FeatureRecord`。P1E-2 后预测端会在 `model.predict()` 前校验模型 metadata 的 `model_input_contract` / `pipeline_signature`，缺失或不匹配时返回 `model_input_mismatch`，不生成假的 SSC/TA/pH；有 Model Studio 正式模型但 scope 不匹配时不再落到旧 target bundle。
- `host_software/static_ui_prototype_bin/quality_algorithm/`：滤光片配置、暗/白校正、ROI、RGB-DVP2 几何配准标定、背景参考果实分割、registered DVP2 conservative ROI、统一分析管线、特征提取、预处理、模型 IO。P1C-2A 后 `registration.py` 支持棋盘格角点检测、RGB -> DVP2 平面单应性估计、profile JSON 读写、reprojection metrics、设备/分辨率错用校验和可视化输出；运行时 `config/registration_profile.json` 被 Git 忽略，`config/registration_profile.example.json` 只作为不绑定真实设备的参考。P1C-2A.5 后正式 Fruit Mask 方向改为 Background Reference：`background_reference.py` 管理背景图 metadata、sha256 和相机/光照兼容性校验，`background_segmenter.py` 使用 RGB absolute difference + OpenCV Lab distance、固定阈值、形态学和连通域诊断生成 mask；SAM3 生产分割方向已放弃，不进入主程序依赖。P1C-2B 后 `registered_roi.py` 把 RGB fruit mask 经 profile warp 到 DVP2 坐标，并在 DVP2 空间执行 conservative erosion 后供多光谱特征均值使用。P1E-1 后 `analysis_pipeline.py` 提供 `FeaturePipelineConfig` 和唯一正式入口，production 默认要求 Dark/White、Background Reference、calibrated registration 和 registered ROI；P1E-2 后同一入口还生成稳定 `ModelInputContract` 与 SHA-256 pipeline signature，且从实际解析的 profile/filter/background semantics 生成 Actual Contract；legacy/development 兼容必须显式配置。P1E-3 的 deterministic software E2E 已覆盖真实 service 闭环和 fault injection。
- `host_software/static_ui_prototype_bin/training/`：特征 CSV 构建、PLSR/SVR/RF 训练、评估。
- `host_software/static_ui_prototype_bin/model_studio/service.py`：SQLite 数据集、样品、标签、训练实验、候选模型、发布模型管理。
- `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`：P1B 真实硬件与采集系统正式验收规范，逐项覆盖 RGB、DVP2、STM32、风扇、LED3、推杆、滤光轮、Dark/White、多波段、样品台、多视角、数据完整性、metadata、安全和 true capture gate；未现场验证项默认 `NOT TESTED`，真实样品台和完整 true capture 当前为 `BLOCKED`。

## 4. 用户完整工作流（当前代码）

1. 运行 `python launcher.py` 或 EXE。Windows launcher 在初始化后端/硬件前获取 Named Mutex；首实例启动本地服务并打开浏览器，后续实例提示并退出。
2. UI 加载 `/api/status`，获取依赖、当前样品、保存位置、模型和 readiness。设备与维护按 STM32、RGB、DVP2 独立检查；相机 probe 释放句柄后仍保留真实 detected/available。
3. 创建样品：选择正常检测 `inspection` 或训练数据采集 `training_capture`，填写名称、果种/品种、保存父目录和图像子目录。新建样品不要求设备先就绪；训练采集允许新果种/品种且不依赖模型。身份和实际目录名写入 metadata。
4. 正常检测展示 Default/Published/generic 模型摘要，可手动更换；训练采集不绑定 SSC/TA/pH 模型。样品台旋转计划属于 `sample_rotation`，与滤光轮切换独立。
5. True Capture：`POST /api/capture/start` 先做当前计划 readiness，再由 `CaptureCoordinator.run_true_capture()` 执行可选 Dark/White、RGB、DVP2 多波段、metadata 与安全收尾。单视角不要求 SampleStage，多视角因真实样品台协议未知而阻塞。
6. RGB 正式采集使用独立 scientific profile，并回读 actual transport。当前示例请求 1920×1080@5fps YUY2；未压缩 4:2:2 可准入但不是完整 RGB strict lossless。有损/未知传输拒绝。RGB/DVP2 网页 JPEG/cache 只用于预览。
7. Offline/Demo：`/api/complete-capture` 调用 `create_offline_capture_dataset()` 生成模拟 PNG/metadata/views；模拟回 Home 不能当作真实样品台完成。
8. 本次拍摄目录自动进入分析；其他样品先选择父目录，再扫描确认 RGB/多光谱子目录。已有目录可直接分析，不要求创建当前样品。进入 shape/sugar/acid/taste 后隐藏双相机预览。
9. 形态分析创建后台任务并轮询结果，主要为 RGB 二维像素面积/宽高/颜色/果粉；旧 RGB-D/PLY 仅兼容，真实尺寸标定与三维硬件尚未完成。
10. SSC/TA/pH 通过共享 `run_feature_pipeline()` 提取特征，校验生产分析依赖与模型输入 contract/signature，再预测；缺模型、缺依赖或输入不匹配明确失败，不生成假值。
11. 检测结果与样品、源文件、标定、背景参考、配准、管线和模型 provenance 写入独立 Inspection SQLite，可查看/归档。有效 SSC/TA 用于糖酸比和口感分析。
12. 导出当前为前端 TXT；正式 PDF/科研报告未完成。TXT 的现有说明仍含“未接入 CaptureCoordinator”旧文案，这是待修正的产品文本，不能当作当前软件链路结论。

`trueCapturePrepared` 表示当前计划 readiness；`HARDWARE_ACCEPTANCE_NOT_PASSED` 与 `productionAccepted=false` 仍保留。当前软件入口存在，正式硬件验收表尚未全部 PASS。

## 5. Model Studio 工作流

当前 Model Studio 已有真实后端和 UI：

1. 从主界面点击“模型训练”，打开 `/model-studio`。
2. 新建 Dataset：填写名称；果种、品种可手动填写，也可在导入第一批带 `metadata.json` 的 Sample 时由 Sample identity 建立；可选默认导入来源通过系统“选择文件夹”写入只读路径框；`ModelStudioService.create_dataset()` 在 SQLite 登记元数据，并创建本地托管目录 `model_studio_data/datasets/<dataset_id>/samples/` 与 `labels.csv`。
3. 导入样品：用户通过系统“选择文件夹”选择主程序保存的 Sample Folder，后端先用 `validate_sample_folder()`/`inspect_sample_structure()` 检查 RGB、多光谱、暗/白校正和 `metadata.json`，返回 Valid/Warning/Invalid。
4. 确认导入后，`import_samples()` 先读取每个 Sample 的 `metadata.json`，优先使用其中的 `sample_id`、`sample_name`、`fruit_type`、`variety`、`sample_mode` 和 capture time；只有旧数据缺少 fruit/variety 时才 fallback 到 Dataset scope。Dataset 为空 scope 且导入样品只有一个 fruit_type/variety 时，Dataset scope 自动继承 Sample identity；同一批或既有 Dataset 中混入多个 scope 会失败为 `DATASET_SAMPLE_SCOPE_CONFLICT`，不静默混合训练。
5. `import_samples()` 使用 COPY 把外部 Sample Folder 复制到 Dataset 本地仓库；SQLite `samples.storage_path`/`local_path` 指向本地副本，`source_path` 只记录原始来源。重复样品默认跳过，可作为新样品导入，不静默覆盖。
6. 导入或录入实验标签：仍支持通过系统“选择文件”选择 `labels.csv`，格式为 `sample_id,ssc,ta,ph`；同时样品详情可手动输入 SSC/TA/pH，只有点击“保存标签”才写入 SQLite。
7. 标签保存：`save_sample_label()` 校验数值，允许部分标签，更新 `labels` 表和 `samples.ssc/ta/ph`，同步 Dataset 本地 `labels.csv`，并标记 Dataset `dirty=1`。
8. 数据质量检查：统计缺失波段、缺失校准、缺失标签、坏图、Excluded/Needs Review 样品。
9. 创建数据集版本：对当前 Included 样品列表、本地路径、Sample identity 和标签值生成 `sample_snapshot_json`/`label_snapshot_json` 与 hash；后续标签修改不会改变旧 Version 的历史含义。
10. 生成特征：`generate_features()` 按 Dataset Version 快照中的本地 `local_path` 调用 shared `run_feature_pipeline()`，输出 `model_studio/artifacts/features/<version>_features.csv`，要求同一版本内样品 `pipeline_signature` 一致，并把本次 `FeaturePipelineConfig`、`model_input_contract`、`pipeline_signature` 写入后续候选模型 metadata，供检测端用同一特征语义生成并校验模型输入。
11. 创建训练实验：选择单一 target（SSC/TA/pH）、模型组合（PLSR/SVR/RF）、预处理组合（RAW/SNV/MSC）、验证方式（GroupKFold 或 TrainTestSplit）；Experiment scope 自动继承 Dataset/Version scope，不默认允许 override 成另一个水果/品种。
12. 启动训练任务：后台线程运行 `_run_training_job()`，对每组预处理/模型组合调用 `training.train.train_one()`。
13. 模型比较：训练结果按 RMSE 排序，模型注册为 `Candidate`，Candidate metadata 继续保存 Dataset/Experiment 继承的 `fruit_type`、`variety` 和 target，文件在 `model_studio/models/candidates/...`。
14. 人工验证/发布：可标记 Validated，可手动 Publish。
15. 设为默认：发布时选择 `setDefault` 或后续点击“设为默认”；只有此时模型才复制到 `trained_models/<target>/` 作为主程序 fallback。
16. 主程序使用 Production/Default：`quality_prediction._select_registry_model()` 先查 Model Studio SQLite 中 Published/Default/Production 模型并校验果种/品种；没有指定/默认模型时才回落到 `trained_models/<target>`。
17. Dataset 维护：Dataset List 和 Dataset Summary 均提供 Archive 与 Permanent Delete。Archive 只隐藏 active list，不删除样品、版本、实验、模型或 lineage；Permanent Delete 先读取 `/api/model-studio/datasets/<dataset_id>/references`，若有 Published/Default/Production 模型引用则阻止，否则可在 Dataset Name 确认后级联清理无生产价值的 Candidate/Validated/Archived 实验数据和受管文件。
18. Model 维护：Model Card 提供“查看 / 重训 / 更多”。更多菜单按 Candidate、Validated、Published、Default、Archived 状态显示 Validate、Publish、Set Default、Archive、Export、Delete Permanently 等合理操作；Default 和仍对应 legacy default bundle 的模型不能直接永久删除。

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
- 当前仓库未提交运行时 Model Studio SQLite；用户本机数据库是否已初始化或含真实记录，本次未核对。
- 预测链路有真实代码和测试；运行时需可用兼容模型、生产分析依赖与有效数据才会输出数值。仓库未提交模型不能直接说明本机预测不可用。

## 7. 已完成 / 部分完成 / 未完成 / 模拟

| 模块 | 状态 | 依据 |
| --- | --- | --- |
| 主工作站静态 UI | DONE | `index.html`/`styles.css`/`app.js` 完整界面与交互 |
| 本地 Python HTTP 后端 | DONE | `backend_server.py` 提供静态资源、API、任务轮询 |
| PyInstaller 打包配置 | DONE | `FruitTasteAnalyzer.spec`、`launcher.py`、`run_analyzer.bat` |
| 样品创建与目录结构 | SOFTWARE IMPLEMENTED | `/api/new-sample` 校验样品身份与保存目录，不依赖硬件先就绪；训练采集模式不依赖模型 |
| 本次拍摄目录进入分析流程 | SOFTWARE IMPLEMENTED / HARDWARE ACCEPTANCE PENDING | 本次拍摄自动进入 analysisDataDir；True Capture 与 Offline/Demo 有独立入口，只有后者生成模拟数据 |
| 样品多角度旋转拍摄计划 | DONE/MOCK | `rotation_plan.py` 计算视角、实际间隔、闭合 View 和 Home 状态；当前无真实样品台电机 |
| 手动选择其他数据目录 | DONE | 主 UI 通过 `/api/select-folder` 选择父目录，再用 `/api/inspect-image-folders` 扫描一级子目录；用户确认 RGB/多光谱目录后由 `/api/sample-folder` 检查 |
| 主程序采集/分析中央布局 | DONE | `app.js` 按模块 key 设置 `layout-capture`/`layout-analysis`；分析模块隐藏相机面板并重排中央内容 |
| 主程序信息架构与仪器级视觉系统 | DONE/PARTIAL | UI-0/UI-1 后左侧导航收敛为检测工作台、样品与记录、设备与维护、模型训练、系统设置五个一级工作区；新增 Operator Workbench 总览卡，基于现有 state 显示当前样品、系统就绪、流程步骤和下一步 Primary Action；视觉层新增兼容 design token 与 Primary/Secondary/Ghost/Danger/Disabled/Focus 规则。底层后端、相机、STM32、采集、模型逻辑未改变 |
| 全局系统状态 | DONE | `app.js deriveSystemStatus()` 基于设备、样品、离线验证、形态任务、SSC/TA/pH 分析和预测结果派生顶栏状态；不会显示假的真实采集状态 |
| 一键设备检查 | DONE/PARTIAL | 设备准备页“开始设备检查”调用 `/api/device/check`，STM32、RGB、DVP2 独立检查；STM32 未连接或缺 pyserial 不阻断 RGB/DVP2；RGB 通过 `CameraManager`/`RgbUvcCamera` 按当前配置 probe，可在实机上显示 3840x2160 @25fps；probe 后释放句柄不会清空 `detected/available`；多光谱状态来自 DVP2 adapter，已接入 probe/预览/参数能力，但不代表完整真实采集已就绪；标定需人工确认 |
| 模型普通/高级模式 | DONE | 样品采集页新增检测模型摘要，默认隐藏 model_id 等高级选择；更换模型后显示原有手动下拉框，继续复用 Default/generic/model_missing 逻辑 |
| 路径选择 UI | DONE | 主程序保存位置/其他样品文件夹、Model Studio 导入来源/样品文件夹/labels.csv 均为只读路径显示 + 系统选择按钮 |
| RGB + 多光谱目录检查 | DONE | `inspect_sample_folder()` 按启用波段检查 |
| Camera Service 基础层 | DONE/PARTIAL | `camera_service` 定义统一接口、异常和 `CameraManager`；adapter 与样品保存目录解耦；`CameraManager` 用单实例和锁统一 self-test、preview、apply settings |
| CaptureCoordinator 骨架 | DONE/PARTIAL | `capture_coordinator.py` 定义状态机、步骤模型、错误模型、取消、超时、best-effort safe stop 和 metadata 骨架；P1B-2 已通过 `HardwareController` 接入安全准备链；P1B-3 新增受保护 RGB 单帧正式采集；P1B-4 新增 DVP2 raw mono 单帧；P1B-5 新增滤光轮+DVP2 多波段 sequence；P1B-6 新增 Dark/White reference、`CalibrationSet` 和 compatibility；P1B-7 新增 `run_sample_multiview_capture()`，用 `SampleViewPlan`/`SampleMultiViewPlan` 编排一个 sample 的多个 View，每个 View 先确认样品台姿态，再采 RGB 和复用 P1B-5 multispectral sequence，记录 view/sample completeness、partial/cancel/return home；`DeviceManager.capture_status()` 暴露 snapshot；P1B-8 的 `/api/capture/start` 按动态 readiness 执行，硬件验收与生产放行仍待完成 |
| RGB UVC/DirectShow adapter | DONE/PARTIAL | `RgbUvcCamera` 使用 OpenCV `cv2.CAP_DSHOW`，返回 RGB `uint8` H×W×3；当前电脑已验证 `device_index=1`、`MJPG`、`3840x2160`、`25fps`；状态明确区分 `detected`、`available`、`opened`、`streaming`；能力探测区分 exposure/gain/white balance 是否实际可设；已接入相机设置页重新检测、参数应用和 960x540 latest-frame/latest encoded JPEG 预览，并通过 `CameraManager.capture_rgb_frame()` 供 Coordinator 受保护单帧正式 PNG 保存使用 |
| DVP2 多光谱相机 adapter | PARTIAL/REAL VERIFIED BY USER | `dvp2_binding.py` 已按真实 `DVPCamera.h`/官方示例绑定 `dvpRefresh`、`dvpEnum`、`dvpOpenByName`、`dvpOpenByUserId`、`dvpStart`、`dvpGetFrame`、曝光/增益/ROI/触发等接口；`Dvp2MonoCamera` 已能发现 SDK、按 serial/user_id 选择目标、打开、开始取流、保留 mono `uint8/uint16` 帧，并接入状态/probe/预览 API；相机设置页已支持网页低延迟预览和曝光/增益真实下发/回读；DVP2 预览由后台 latest-frame/latest encoded JPEG cache 服务浏览器，不进入 scientific capture；P1B-4 已通过 `CameraManager.capture_multispectral_frame()` 接入 Coordinator 受保护 raw mono 单帧正式保存；P1B-5 复用同一 raw frame 边界做滤光轮同步多波段 sequence 保存；P1B-6 复用同一边界做 Dark/White reference raw sequence 保存；PixelFormat 仅显示当前实际值，当前只验证 `Mono8`，不开放格式切换；暗白物理校正仍未现场验收 |
| RGB 二维形态/表面分析 | DONE/PARTIAL | 可测面积、宽高、颜色、果粉；不是完整真实尺寸标定 |
| RGB-D/PLY 点云兼容 | PARTIAL | 旧流程可用，主 UI 标为三维建模预留 |
| 多光谱特征提取 | DONE | 暗/白校正、ROI 均值、波长校验 |
| RAW/SNV/MSC | DONE | `preprocessing.py` |
| PLSR/SVR/RF 训练 | SOFTWARE IMPLEMENTED / SCIENTIFIC VALIDATION PENDING | 有按 sample_id 分组的外层验证与 fold 内预处理；PLSR 成分数仍按训练误差选择，内层 CV 待补 |
| Model Studio 数据集/版本/训练/发布 | DONE/PARTIAL | 后端和 UI 已整理为 Dashboard / Datasets / Training / Models / Settings；Dataset 已本地托管并支持后续 Add Samples、Include/Exclude、Sample 引用保护 Permanent Delete、Dataset Archive、带生产模型依赖保护的 Dataset Permanent Delete、不可变 Dataset Version、Version Diff、Experiment/Run/Variant、Model Card Registry、状态化 Archive/Delete、Retrain lineage；仓库未提交真实数据/运行时数据库，本机数据状态未核对 |
| Production 模型人工发布 | DONE | `publish_model()`/`set_default_model()`；复制到 `trained_models/<target>` |
| 主程序按果种/品种选模型 | DONE | `/api/quality-models`、`resolve_model_id()`、`_select_registry_model()` |
| SSC/TA/pH 预测入口 | DONE/PARTIAL | 真实加载模型预测；P1E-1 后模型输入前的 FeatureRecord 通过 shared analysis pipeline 生成；P1E-2 后先校验 `model_input_contract` / `pipeline_signature` 再进入模型预测，缺少 production 分析依赖时返回 feature_error，缺少或不匹配 contract 时返回 model_input_mismatch，不伪造 SSC/TA/pH；当前无生产模型时返回缺失 |
| 糖酸比/口感分析 | PARTIAL | 前端根据预测值计算，等级规则较简单 |
| 真实相机 SDK | PARTIAL | RGB OpenCV/DirectShow adapter 已有并可预览；DVP2 已完成真实 SDK adapter、manual test 用户实机通过、网页预览/曝光/增益 API 已接入；RGB 单帧、DVP2 单帧、滤光轮+DVP2 多波段 sample sequence、Dark/White calibration sequence、Sample MultiView orchestration 和 P1B-8 单视角 `/api/capture/start` 软件入口已有；真实样品台、多视角硬件采集、hardware acceptance 和真实硬件检测验收仍未完成 |
| 真实电机/滤光轮串口 | PARTIAL / HARDWARE ACCEPTANCE PENDING | 默认 AA55 adapter 已有 ACK/fresh STATUS、slot→degrees+rpm 映射、人工 SET_ORIGIN 与安全收尾；旧两字节层仅兼容。逻辑位置不等于物理编码器反馈，真实滤光轮现场验收仍待完成 |
| 真实光源控制 | PARTIAL/REAL VERIFIED | PB7/PB8 两路钨灯 SSR 已通过 `manual_stm32_test.py --led-mask` 实机确认；主 UI 已提供安全手动测试入口，后端限时自动关闭并用 fresh STATUS 确认。PB9/LED3 仍可独立 on/off 且不会清除钨灯位。亮度闭环、双钨灯同时开启、RGB 正式照明映射、硬件级门联锁和最终光源验收仍未完成 |
| 门控/急停/温度/报警 | PARTIAL/TODO | 升降门、急停、故障码已有控制/查询；温度和报警扩展未接入 |
| Production Analysis Pipeline | DONE/SOFTWARE IMPLEMENTED | P1E-1 新增 `quality_algorithm.analysis_pipeline.FeaturePipelineConfig` / `run_feature_pipeline()` 作为训练端和检测端共享入口。P1E-2 新增 `ModelInputContract` 和 `pipeline_signature`，训练生成并写入模型 metadata，发布/Default 和预测前校验；Published/Default 还要求 `model_input_contract.mode=production`。P1E-3 在临时 SQLite 与 deterministic synthetic data 上完成训练 -> 发布 -> 检测 software E2E PASS。Production 默认要求 Dark/White calibration、Background Reference segmentation、calibrated RGB->DVP2 registration、registered multispectral ROI 和完整波段；缺失依赖明确失败。Legacy/development 旧数据路径保留，但必须显式配置。真实硬件和科学验收仍未完成 |
| 标定配准 | PARTIAL/SOFTWARE IMPLEMENTED | 暗/白校正有；P1C-2A 新增 RGB -> DVP2 平面单应性离线标定模块和 `manual_registration_test.py`；P1C-2B 新增 registered multispectral conservative ROI，把 RGB Fruit Mask warp 到 DVP2 坐标后 post-warp erosion，并接入 `extract_feature_record(registration_mode="calibrated")`；P1E-1 将该链路纳入 production analysis pipeline 默认要求。真实几何和 ROI overlay 仍待硬件验收 |
| 历史记录数据库/正式报告 | DONE/PARTIAL | P1E-4 新增独立 `inspection/inspection.sqlite`、Inspection/Prediction Result schema、runtime provenance、列表/详情/归档 API 和结果区轻量历史查看；正式 PDF/科研报告仍未完成 |
| Software-enforced Model Quality Gate | DONE/SOFTWARE IMPLEMENTED | P1E-5 新增可配置 `ModelQualityPolicy` / `ModelQualityReport` / `evaluate_model_quality()`；训练 metadata 保存 sample count、validation metrics、feature statistics 和 target range；Publish/Default 在既有 production contract gate 后执行质量门禁并持久化报告。默认值是 placeholder/software gate defaults，不等于 scientific accuracy validated、real Production model approved 或 hardware acceptance completed |
| Software Productization Baseline | DONE/SOFTWARE IMPLEMENTED | P1E-6 提供 versioned SQLite migrations + pre-migration backup、restart reconciliation、runtime config bootstrap/validation、atomic JSON writes、rotating logs、health endpoint、Windows CI 与 PyInstaller smoke；冻结 EXE 的 mutable data 使用 `%LOCALAPPDATA%\FruitTasteAnalyzer\app_data`，不写入 Program Files/resource directory。该状态只代表 software productization baseline，不代表 Production Ready、hardware acceptance 或 scientific validation |

## 8. 关键设计决策

这些决策已经由代码、测试或项目要求体现，后续不要随意推翻：

- 不推倒现有 UI 重做；在当前三栏工作站界面基础上优化。
- 主程序 UI 后续修改必须遵循 `docs/UI_ARCHITECTURE.md` 和 `docs/UI_DESIGN_SYSTEM.md`；保持 Operator Workflow 与 Engineer Workflow 分离，不把工程调试入口重新堆回普通检测流程，不无理由改变现有 Dark Slate + Purple/Fuchsia/Cyan 配色体系。
- 主程序继续以 Python 上位机为核心。
- 当前前端是静态 HTML/CSS/JS，后端是 Python 本地 HTTPServer；历史文档中的 Vue/FastAPI/Electron 是早期建议，不是当前实现。
- RGB 相机第一阶段走 OpenCV `cv2.CAP_DSHOW`/Windows DirectShow/UVC，CameraService 返回 RGB `uint8` numpy 帧，不直接决定样品目录或文件名。当前电脑实机验证默认配置为 `device_index=1`、`MJPG`、`3840x2160`、`25fps`，但该 index 是配置层默认值，不是跨电脑稳定身份。
- 正式 scientific capture 图像统一使用 lossless PNG；RGB、DVP2 单帧、多波段 sample、Dark/White reference、Sample MultiView 和离线验证数据的正式 metadata 不得引用 `.jpg/.jpeg`。JPEG 只允许用于浏览器 preview。RGB 正式采集还必须通过 PNG 之前的 source transport gate：actual FOURCC 为 MJPG/JPEG/H264/H265 时 FAIL，YUY2/YUYV/UYVY 因 4:2:2 chroma subsampling 不算 strict full RGB lossless 但允许作为 `uncompressed_422` scientific capture，并在 metadata 中诚实记录 `scientificStrictLossless=false`。
- P1B 已进入 hardware acceptance 阶段；真实采集放行必须以 `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md` 中关键域全部 PASS 为前提。没有现场证据时，STM32、风扇、LED、推杆、滤光轮、RGB 浏览器端延迟、DVP2 网页预览、Dark、White、样品旋转台和完整真实采集都不能标记 PASS。
- DVP2 多光谱相机是 DO3THINK/度申 GigE/RJ45 工业黑白相机；当前绑定只能使用已从真实 `DVPCamera.h` 和官方示例确认的 C API，禁止凭经验新增未核对函数名，禁止用 OpenCV VideoCapture 替代，禁止返回模拟帧。
- 当前设备准备分为两层：`devicePrepared` 表示离线验证流程可用，`trueCapturePrepared` 表示当前 `TrueCapturePlan` 是否通过真实入口 readiness gate。P1B-8 后它不再硬编码 false，但只代表当前软件编排可尝试执行；`HARDWARE_ACCEPTANCE_NOT_PASSED` warning 和 `productionAccepted=false` 继续说明这不是硬件验收 PASS。
- 设备发现和设备绑定不等于设备就绪：`discovered/selected/bound/connected/verified/ready` 必须区分。`COM5`、`COM7`、`device_index=1`、`device_index=2` 只能作为 current location 或 last known location cache；不能作为跨电脑永久身份。默认 STM32 adapter 已用 AA55 handshake/STATUS/INFO 验证当前 firmware profile；旧两字节 PING 保留兼容边界，不能当作永久身份或跨硬件角色证明。
- 样品旋转角度和滤光片转轮角度必须完全独立：`sample_rotation` 控制样品台多视角，`filter_wheel_rotation` 控制多光谱波段切换，不能用同一字段或同一电机状态表示。
- 多角度采集默认不拍 360°，因为 0° 与 360° 是同一位置；只有用户启用闭合补拍时才生成 `closure_view=true` 的额外 View。
- 多 View 仍属于同一个水果 Sample，同一个 `sample_id`；后续如果展开为多行特征，训练/验证必须继续按 `sample_id` 分组，避免同一水果进入 train/test 两边。
- 第一版模型以 PLSR 为基线，SVR 作为主要对照，RF 作为额外比较模型。
- 预处理支持 RAW / SNV / MSC。
- 训练数据不足时必须失败，不能造假标签或伪造模型结果。
- Production/Default 模型必须人工发布；系统不能自动替换正式模型。
- Sample Identity 不依赖 Model Availability。主工作站支持 `sample_mode=training_capture` 在没有任何 Published/Default/Production 模型时创建训练采集样品，`fruit_type`/`variety` 写入 Sample metadata，SSC/TA/pH 选择模型 ID 合法保持为空。
- 同一 fruit type + variety + target 只能有一个 Default；Model Studio 可从 Published/Production 模型切换 Default，旧 Default 自动保留为 Published，不删除。
- 支持不同水果/品种使用不同模型，并允许 `generic` 品种兜底。
- 本次拍摄目录可以自动进入分析流程。
- 同时允许用户手动选择其他数据目录。
- 文件夹/文件路径选择优先使用系统原生选择器，UI 只显示只读路径；取消选择不得清空旧路径。
- 候选模型必须与 Production/Default 隔离。
- 模型输入波长必须与 metadata 匹配；校准要求必须由模型 metadata 控制。
- 当前 `quality_algorithm/filter_config.development.json` 是开发离线配置，正式训练前必须替换为实测滤光轮配置。

## 9. 当前技术债务

代码和文档中确认的主要缺口：

- 真实采集闭环仍需现场验收：STM32 串口、滤光轮、推杆/门控、急停、风扇和 LED3 已有控制层、P1B-7.5A.1 current-firmware profile 绑定和 P1B-7.5A.2 Web 调试入口/fresh STATUS 运动验证；RGB adapter 已完成当前电脑实机验证，并接入相机设置页 latest-frame 预览/参数应用和正式 PNG 保存；DVP2 adapter 已完成真实 SDK 打开/取帧边界、用户实机 manual test 通过，已接入相机设置页 low-latency latest encoded JPEG 预览/曝光/增益回读、raw mono PNG 保存、滤光轮同步多波段 sample sequence、Dark/White calibration sequence 和 Sample MultiView orchestration 软件路径；P1B-8 已放行单视角 `/api/capture/start` 软件入口，但真实 STM32 Web 控制现场 smoke、暗白物理校正验收、样品台真实旋转、多视角硬件采集、温度和扩展报警仍未完成。Windows launcher 现在有单实例 mutex，RGB/DVP2 另有跨进程设备 ownership mutex；设备被占用时应报告 busy 而不是未检测到。
- P1B hardware acceptance checklist 已建立，但尚未填写现场测试证据；这份文档是放行门槛，不是 PASS 证据本身。
- 样品旋转平台已有角度计划、UI、metadata、离线模拟文件、P1B-7 `SampleStage` 软件抽象和 P1B-7.5B status/API/UI 调试边界；仓库内未找到独立样品台控制器协议，仍缺少真实控制板身份、通信方式、HOME、位置回读、稳定确认和报警/超时 contract 的硬件实现。
- `create_offline_capture_dataset()` 会写模拟 RGB/多光谱/暗白图片，只能用于离线验证。
- 主 UI 的串口刷新/连接、一键设备检查、非破坏硬件通信自检、风扇 on/off、LED3 on/off、两路钨灯限时手动测试/立即关闭/全部钨灯关闭、推杆伸出/缩回/停止、滤光轮顺/逆时针相对移动、滤光轮 STOP、人工 SET_ORIGIN 和紧急停止已接后端设备 API；P1B-7.5B 新增样品旋转台状态和调试按钮，但默认因 `SAMPLE_STAGE_PROTOCOL_UNKNOWN` 禁用真实动作。P1B-8 在样品采集页新增 True Hardware Capture 面板；RGB 与 DVP2 相机设置页预览已接入；真实样品台旋转、RGB 正式照明映射、双钨灯验收和完整多视角硬件同步仍未完成。
- P1C-2B 已把 P1C-2A 的 RGB-DVP2 profile 和 P1C-2A.5 的 RGB Fruit Mask 接入多光谱特征提取：calibrated mode 必须显式提供 RegistrationProfile 和 runtime RGB/DVP2 endpoint metadata，缺失或 mismatch 会失败；禁止 resize、identity fallback、ellipse fallback。Planar homography 不是 3D fruit surface 的 pixel-perfect registration，所以 post-warp erosion 在 DVP2 coordinate space 执行，用于降低边缘背景/托盘/阴影污染风险；REAL HARDWARE ROI ACCEPTANCE PENDING。
- P1E-1 已把 Model Studio feature generation、training feature CSV、主程序 SSC/TA/pH 预测入口统一到 `run_feature_pipeline()`。P1E-2 已加入稳定模型输入 contract/signature，训练 metadata、发布/Default 和预测前校验均覆盖；P1E-3 software E2E 已在 fake/deterministic environment 下 PASS。Production 默认不再静默 fallback 到 uncalibrated、legacy color segmentation、identity registration 或无 contract 旧模型；历史/离线数据仍可通过显式 `FeaturePipelineConfig.legacy()` / `development()` 或 legacy compatibility policy 处理。真实 Background Reference 兼容性 metadata、registration profile 现场采集、ROI overlay 人工验收仍为硬件/算法验收待办，不能声称覆盖所有水果或达到固定 Mean IoU 指标。
- 当前仓库未提交真实 Production 模型文件；运行时无可用模型时 SSC/TA/pH 返回 `model_missing`，用户本机模型状态未核对。
- P1E-5 的 Model Quality Gate 只建立可配置的软件检查机制。当前默认 policy 不写死最终科研 R²/RMSE/MAE/RPD 标准；真实水果实验、科学阈值、OOD 现场验证和 Production approval 仍待完成。
- 当前没有提交真实样品图像数据；`sample_data/README.md` 说明不再内置 demo 图像目录。
- Model Studio 已能删除 Sample 记录或同时删除本地托管副本，并保留 `source_path` 原始目录；Dataset 级 Archive / Permanent Delete、Model 单删和批量 Permanent Delete 已实现，Default/Production/reference 保护仍由后端 service 层执行，仍需真实用户数据场景下做人工 UI 验收。
- 仓库未提交运行时 Model Studio SQLite；不能据此推断用户本机没有数据集或模型记录。
- P1E-4 后主程序检测结果写入独立 `inspection/inspection.sqlite`；记录 Sample、原始文件引用、Calibration、Background Reference、Registration、实际 pipeline contract/signature、模型 provenance 和 SSC/TA/pH 结果。历史记录是软件运行事实快照，不跟随当前 Default Model 或 Background Reference 变化；正式 PDF/科研报告仍未完成。
- 报告导出是前端 TXT；检测历史已经独立写入 SQLite，正式 PDF/科研报告仍未完成。TXT 中关于 CaptureCoordinator 尚未接入的旧说明待修正。
- 形态分析当前以像素尺度为主，未完成真实尺寸标定和三维硬件方案。
- 旧 `pipeline_v2.py` 和 `pointcloud_service.py` 中仍有兼容 RGB-D/点云的遗留路径，需避免误认为当前主硬件已经有深度相机。

## 10. 下一阶段开发路线

### 软件与研究可先推进

- 已有共享 production 特征管线、输入 contract、质量门禁、检测历史和产品化基础，不再将这些列为“从零新增”。
- 改进 `training/train.py` PLSR 成分数选择：保留 sample_id 外层分组验证，在训练折内做内层分组 CV 调参；预处理也需按相应训练折拟合。
- 核查真实 SSC/TA/pH 标签、样品编号和异常样品证据，保留 Include/Exclude 与版本 lineage，不因删除后指标提高就判定异常。
- 若实现 VIP/CARS/SPA 研究模块，使用连续光谱与真实标签、在训练 fold 内筛选，记录每折波长/稳定性/误差；目前已核对入口没有这些实现。
- 现有指标为 R²/RMSE/MAE/RPD，MAPE 与 100%−MAPE 可作为后续辅助指标，不能代替真实外部验证。
- 核对 RGB YUY2 后端准入与前端 strict-lossless PASS/FAIL 文案，修正 TXT 中 CaptureCoordinator 旧说明；正式 PDF/报告模板后续开发。

### 硬件与数据具备条件后

- 按 `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md` 填写现场证据：当前软件 readiness 与正式生产放行分开，未测项目保持 NOT TESTED/BLOCKED。
- 确认 RGB scientific profile 的 actual FOURCC、DVP2 raw mono、暗白质量、滤光轮同步、照明互锁、安全收尾和 metadata；几何 profile 与保守 ROI 做真实 overlay 验收。
- 补齐独立 SampleStage 控制器身份、协议、HOME、position/fault contract，再实现真实多视角 adapter，禁止复用滤光轮电机。
- 正式波段需依据连续光谱研究、滤光片中心波长/带宽、相机响应和光源确定，并用真实多光谱重采数据验证；开发配置 450/560/670 nm 不能当作最终设计。
- 用真实数据验证托管导入、实测标签、冻结版本、训练、人工发布/设默认、检测及历史记录。当前仓库未提交真实模型不等于用户本机没有模型。
- 继续完善尺寸标定/三维边界、报告、批次、日志和 EXE 现场部署；软件 E2E/fake adapter 测试不替代硬件或科学验收。
