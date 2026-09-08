# Architecture

更新时间：2026-09-08

## 总体结构

```text
host_software/static_ui_prototype_bin/
  launcher.py
  backend_server.py
  index.html / styles.css / app.js
  serial_service.py
  stm32_protocol.py
  stm32_controller.py
  device_discovery.py
  hardware_controller.py
  device_manager.py
  pointcloud_service.py
  pipeline_v2.py
  rotation_plan.py
  capture_coordinator.py
  camera_service/
  quality_prediction.py
  quality_algorithm/
  training/
  model_studio/
  sample_data/
  trained_models/
  tests/
```

运行时结构：

```text
launcher.py
  -> start_backend()
  -> ThreadingHTTPServer 127.0.0.1:<free_port>
  -> Browser UI
  -> JSON APIs
```

## 模块 -> 文件 -> 类/函数

| 模块 | 文件 | 关键类/函数 | 输入 | 输出/副作用 | 依赖 |
| --- | --- | --- | --- | --- | --- |
| 桌面启动 | `launcher.py` | `prepare_runtime_site()`, `main()` | 打包资源或源码目录 | 启动后端，打开浏览器 | `backend_server.start_backend` |
| HTTP 后端 | `backend_server.py` | `start_backend()`, `create_handler()` | 静态目录、输出目录、app_dir | 本地 HTTP 服务 | Python stdlib, PIL |
| 路径选择与校验 | `backend_server.py` | `select_directory_dialog()`, `select_file_dialog()`, `validate_folder_path()`, `validate_file_path()` | 选择用途、初始目录、用户系统选择结果 | `/api/select-folder`、`/api/select-file` 返回只读路径和校验状态 | tkinter / PowerShell fallback |
| 作业队列 | `backend_server.py` | `JobStore` | job 状态更新 | `/api/jobs/<id>` | threading |
| 样品会话 | `backend_server.py` | `SessionState` | 样品表单、模型选择、目录 | 当前样品状态 | Model Studio 可选 |
| 设备准备状态 | `backend_server.py`, `app.js` | `SessionState.update_device_preparation()`, `DeviceManager.capture_readiness()` | 连接/电机/光源/相机/标定检查状态与当前 true-capture plan | `/api/device-preparation`，`devicePrepared` 表示当前离线验证可用；P1B-8 后 `trueCapturePrepared` 来自当前 `TrueCapturePlan` readiness，不再硬编码 true，也不代表 hardware acceptance PASS | 串口/滤光轮部分可走真实 API，RGB adapter 已实机验证并接入正式 PNG 保存，DVP2 adapter 已接入 raw mono PNG、多波段、Dark/White、Sample MultiView；真实多视角仍被 SampleStage protocol gate 阻断 |
| 全局系统状态 | `app.js` | `deriveSystemStatus()`, `renderSystemStatus()` | `state`、设备状态、样品状态、形态任务、预测任务 | 顶栏“当前状态” | 前端派生状态，不新增后端状态源 |
| STM32 串口 | `serial_service.py` | `SerialService` | 串口名、超时、旧两字节命令或 raw bytes | 串口 open/read/write/clear buffers、旧两字节 RESULT、异常 | pyserial |
| STM32 current firmware protocol | `stm32_protocol.py` | `CURRENT_STM32_FIRMWARE_PROFILE`, `CURRENT_FILTER_WHEEL_MAPPING`, `Stm32ProtocolProfile`, `FilterWheelMapping`, `Aa55Codec`, `Aa55StreamParser`, `crc16_ccitt_false()`, `decode_status_payload()`, `decode_info_payload()` | 当前固件 AA55 profile、AA55 byte stream、STATUS/INFO payload | AA55 frame、CRC 校验、partial/multiple/noise/CRC error stream parse、STATUS/INFO 解码边界；MOVE payload 为 float32 LE degrees + rpm，PPR 只作诊断 | dataclass |
| STM32 current firmware adapter | `stm32_controller.py` | `Stm32ControllerAdapter`, `Stm32StatusSnapshot`, `Stm32CommandResult`, `Stm32SafetyReport` | 单一 `SerialService` owner、`CURRENT_STM32_FIRMWARE_PROFILE`、`CURRENT_FILTER_WHEEL_MAPPING`、语义 fan/LED/door/filter wheel action | ACK echo correlation、STATUS/INFO cache revision、handshake、PPR consistency diagnostic、command serialization、fresh STATUS mechanical verification、safe_stop 动作报告；可作为 `HardwareController` transport 注入 | SerialService, stm32_protocol |
| 设备发现与绑定 | `device_discovery.py`, `device_manager.py`, `backend_server.py`, `app.js` | `DeviceDiscovery`, `DeviceRegistry`, `DeviceBinding`, `DeviceCandidate` | 当前串口列表、RGB DirectShow index 扫描、Windows PnP/UVC 元数据、DVP2 SDK 枚举、用户角色选择 | `/api/devices/discover` 返回候选和域诊断；`/api/devices/bindings` 返回 profile 和匹配；`/api/devices/bind` 按角色/kind 校验并保存运行时绑定到 `runtime/hardware_profile.json` | SerialService, CameraManager, DVP2 binding |
| 硬件控制 | `hardware_controller.py` | `HardwareController`, `CapabilityUnavailableError` | 风扇、升降门、RGB LED、钨灯、滤光轮、急停、故障清除 | 语义动作和状态查询；当前 firmware adapter 声明 tungsten unsupported 时非零钨灯请求明确失败 | SerialService 或 Stm32ControllerAdapter |
| 设备管理 | `device_manager.py`, `backend_server.py` | `DeviceManager`, `DeviceManager.self_test()`, `set_fan()`, `set_led3()`, `actuator_extend()`, `actuator_retract()`, `actuator_stop()`, `move_filter_wheel()`, `stop_filter_wheel()`, `set_filter_wheel_origin()`, `sample_stage_status()`, `sample_stage_home()`, `sample_stage_move_absolute()`, `sample_stage_move_relative()`, `sample_stage_stop()` | 串口连接、自检、状态、急停、采集状态、Web 硬件调试命令、独立样品旋转台边界 | `/api/device/*`, `/api/capture/*`，self-test `checks`；风扇/LED3/推杆/滤光轮 Web 控制统一走 DeviceManager，不从 HTTP handler 直接访问协议；SampleStage API 也只通过 DeviceManager，默认返回 `SAMPLE_STAGE_PROTOCOL_UNKNOWN` | HardwareController, Stm32ControllerAdapter, SampleStage |
| 采集协调器 | `capture_coordinator.py`, `device_manager.py` | `CaptureCoordinator`, `TrueCapturePlan`, `CaptureRun`, `CaptureStep`, `CaptureStepPlan`, `CaptureReferenceType`, `CalibrationSet`, `SampleViewPlan`, `SampleMultiViewPlan`, `MultispectralBandPlan`, `MultispectralCapturePlan`, `run_true_capture()`, `run_rgb_capture()`, `run_multispectral_capture()`, `run_multispectral_sequence()`, `run_dark_reference_capture()`, `run_white_reference_capture()`, `run_sample_multiview_capture()`, `validate_calibration_compatibility()` | 注入的 CameraManager/DeviceManager/HardwareController/SampleStage、TrueCapturePlan、样品 ID、输出目录、filter config 或显式 band plan、operator confirmation | JSON-friendly snapshot、取消/失败/超时状态、metadata、best-effort safe stop；P1B-8 `run_true_capture()` 编排 existing/capture_new calibration、单视角 RGB + multispectral sequence 和多视角 gate，不复制相机/滤光轮/DVP2 saver | dataclass, Enum, PIL, numpy |
| 样品台高层边界 | `sample_stage.py` | `SAMPLE_STAGE_PROTOCOL_UNKNOWN`, `SampleStageStatus`, `SampleStagePosition`, `UnimplementedSampleStage`, `SimulatedSampleStage` | connect/disconnect、HOME、绝对/相对角度、STOP、safe_stop、状态与位置回读 | 默认 hardware adapter 明确报告 `SAMPLE_STAGE_PROTOCOL_UNKNOWN`，不伪造 controller/transport/HOME/position feedback；simulation 只供 unittest/离线软件编排验证 | dataclass |
| 相机服务接口 | `camera_service/base.py`, `camera_service/errors.py` | `CameraDeviceInfo`, `CameraFrame`, `CameraStatus`, `CameraError` | 相机 adapter 状态、帧、参数请求 | 统一状态/异常；`CameraStatus` 区分 `detected`、`available`、`opened`、`streaming`；frame 不绑定样品目录 | Python dataclass/protocol |
| RGB UVC 相机 | `camera_service/config.py`, `camera_service/rgb_uvc.py` | `RgbCameraConfig`, `RgbUvcCamera` | OpenCV device_index、DirectShow capture、请求 width/height/fps/fourcc/exposure/gain/white balance | RGB `uint8` H×W×3 numpy 帧；probe 成功后释放句柄仍保留 `detected/available`；status 同时返回 `requested`、`actual`、`capabilities` 和 `transport=UVC/DirectShow`；支持 apply config | `cv2`, numpy |
| DVP2 多光谱相机 | `camera_service/dvp2_binding.py`, `camera_service/dvp2_mono.py` | `Dvp2Binding`, `Dvp2MonoCamera`, `find_dvp2_sdk()`, `frame_to_array()` | `DVP2_SDK_DIR`、配置路径、`DVPCamera64.dll`、真实 `DVPCamera.h`/官方示例、GigE 设备枚举信息 | `dvpRefresh/dvpEnum` 真实枚举、按 serial/user_id 选择目标、打开、状态、ROI/曝光/增益/触发/帧转换接口；`capture_frame()` 保留 mono `uint8/uint16` raw dtype；`connected` 作为 detected 兼容别名；已发现但无法打开时提示 BasedCam3/其他程序占用；当前只验证 `Mono8`，不开放 PixelFormat 切换 | `ctypes`, numpy, pathlib |
| 相机管理器 | `camera_service/manager.py` | `CameraManager.status()`, `CameraManager.checks()`, `probe_rgb()`, `probe_multispectral()`, `apply_rgb_settings()`, `apply_multispectral_settings()`, `capture_rgb_frame()`, `capture_multispectral_frame()`, `start_rgb_preview()`, `rgb_preview_jpeg()`, `start_multispectral_preview()`, `multispectral_preview_jpeg()`, preview stop methods | RGB adapter、多光谱 adapter、RGB/多光谱参数 payload、preview 参数 | `/api/status` camera 状态、设备检查相机项、RGB/DVP2 probe 结果、RGB requested/actual、多光谱曝光/增益回读、RGB 与多光谱 JPEG 预览帧；RGB/DVP2 preview 均由后台 latest-frame + latest encoded JPEG cache 驱动，HTTP 请求直接返回最新 JPEG cache 并允许 drop old frame；`capture_rgb_frame()` 通过同一 RGB adapter 返回正式 RGB `CameraFrame` 和状态快照；`capture_multispectral_frame()` 通过同一 DVP2 adapter 返回正式 raw mono `CameraFrame` 和状态快照，预览运行中复用 stream，预览停止时临时 open/start/capture/stop/close；预览 JPEG 只用于浏览器显示，不改变底层 frame dtype，不作为正式 scientific capture 输入 | RgbUvcCamera, Dvp2MonoCamera, PIL, OpenCV 可选, numpy |
| 样品旋转计划 | `rotation_plan.py`, `backend_server.py`, `app.js` | `build_capture_rotation_plan()`, `mark_plan_completed()`, `renderRotationPlan()` | 期望角度间隔、起始角度、CW/CCW、闭合补拍 | `captureRotationPlan`、`sample_rotation` metadata、`views.json` | math/json |
| 样品目录 | `backend_server.py` | `create_unique_sample_folder()`, `ensure_sample_capture_folder()` | 保存根目录、样品名、metadata | 创建目录和 `metadata.json` | pathlib/json |
| 离线采集 | `backend_server.py` | `create_offline_capture_dataset()` | 样品目录、metadata、`captureRotationPlan` | 写模拟图片、校准图、View metadata | PIL, rotation_plan |
| 主 UI | `index.html` | 页面结构 | 用户操作 | 显示工作站 | `app.js` |
| 主 UI 状态机 | `app.js` | `state`, `api()`, `deriveSystemStatus()`, `runUnifiedDeviceCheck()`, `renderModelOverview()`, `getModuleLayoutMode()`, `applyModuleLayout()`, `createNewSample()`, `runShapeAnalysis()`, `runSscAnalysis()`, `runAcidAnalysis()`, `updateTaste()` | 用户事件/API 响应 | DOM 更新、全局状态、设备检查摘要、采集/分析布局切换、报告 TXT | Browser APIs |
| 图像子目录扫描 | `backend_server.py` | `inspect_image_folders()`, `validate_image_dir_name()`, `validate_direct_child_dir()` | 样品父目录、用户选择的一级子目录名 | 子目录列表、建议角色、目录名校验 | pathlib, pointcloud_service |
| 目录检查 | `pointcloud_service.py` | `inspect_sample_folder()`, `_inspect_sample_folder_by_enabled_bands()` | 样品目录、RGB/多光谱子目录名 | 数据质量报告 | PIL, quality_algorithm |
| 形态分析 | `pointcloud_service.py` | `analyze_rgbd_dataset()`, `analyze_rgb_multispectral_sample()` | 样品目录、输出目录、相机内参 | 形态/表面结果、预览图 | numpy, PIL |
| 旧点云兼容 | `pointcloud_service.py`, `pipeline_v2.py` | `analyze_with_pipeline_v2()`, `reconstruct_sfm()` | RGB-D 目录或 PLY | PLY、点云指标 | OpenCV 可选 |
| 光谱配置 | `quality_algorithm/filters.py` | `FilterBand`, `load_filter_config()`, `enabled_bands()` | JSON 配置 | 启用波段列表 | json |
| 校正 | `quality_algorithm/calibration.py` | `reflectance_correction()`, `normalize_uncalibrated()` | sample/dark/white 灰度图 | 反射率矩阵 | numpy, PIL |
| ROI | `quality_algorithm/roi.py` | `build_rgb_fruit_mask()`, `apply_mask_to_image()` | RGB 图、光谱图 | mask 后像素 | numpy, PIL |
| 特征提取 | `quality_algorithm/spectral_features.py` | `inspect_sample_structure()`, `extract_feature_record()` | 样品目录 | `FeatureRecord` | filters/calibration/roi |
| 预处理 | `quality_algorithm/preprocessing.py` | `PreprocessorState`, `fit_transform_preprocessor()` | 特征矩阵 | RAW/SNV/MSC 后矩阵 | numpy |
| 模型 IO | `quality_algorithm/model_io.py` | `save_model_bundle()`, `load_model_bundle()`, `predict_feature_record()` | 模型目录、FeatureRecord | 预测数值 | joblib, preprocessing |
| 预测入口 | `quality_prediction.py` | `SampleSession`, `PredictionResult`, `predict_ssc()`, `predict_ta()`, `predict_ph()` | 样品目录和模型选择 | 结构化预测结果 | model_io, spectral_features, sqlite |
| 训练数据 | `training/build_dataset.py` | `build_dataset()` | samples root, labels.csv | features.csv | quality_algorithm.dataset |
| 模型训练 | `training/train.py` | `train_one()`, `run_experiment_matrix()` | features.csv、target、model、preprocessing | 模型 bundle、指标 | scikit-learn |
| 模型评估 | `training/evaluate.py` | `evaluate_model()` | features.csv、model_dir、target | R2/RMSE/MAE/RPD | model_io |
| Model Studio | `model_studio/service.py` | `ModelStudioService` | Dataset、样品、标签、实验配置 | SQLite 记录、本地托管样品、features、候选/发布模型 | sqlite3, training |
| Model Studio UI | `model_studio/static/*` | `model_studio.js` | 用户操作 | Dataset/训练/发布页面 | `/api/model-studio/*` |

## API 数据流

### 启动和状态

```text
GET /api/status
  -> dependency_status()
  -> DeviceManager.status()
  -> CameraManager.status()
  -> SessionState.snapshot()
  -> defaultSaveRoot
```

输出包括 Python 依赖、设备准备状态、真实/离线设备状态、顶层 `cameras`、当前样品、当前拍摄目录、分析目录、果种/品种、已选模型。`device.cameras` 与顶层 `cameras` 保持同一状态来源。

### 设备准备

```text
app.js loadDevicePorts()/connectDevice()/runHardwareSelfTest()/emergencyStopDevice()
  -> GET /api/device/ports
  -> POST /api/device/connect
  -> POST /api/device/self-test
  -> POST /api/device/emergency-stop
  -> backend_server device routes
  -> DeviceManager
  -> HardwareController
  -> SerialService
  -> default Stm32ControllerAdapter for current AA55 firmware, with legacy two-byte compatibility available when explicitly disabled

app.js runUnifiedDeviceCheck()
  -> POST /api/device/check
  -> DeviceManager.independent_device_check()
  -> controller check / RGB probe / DVP2 probe are independent domains
  -> checks: controller/door/fan/filterWheel/rgbCamera/multispectralCamera/light/calibration

app.js refreshDeviceDiscovery()/bindSelectedDevice()
  -> GET /api/devices/discover
  -> DeviceManager.discover_devices()
  -> DeviceDiscovery.discover_all()
  -> serial candidates: list ports, skip already connected port, otherwise open/PING/close only
  -> serial dependency missing reports diagnostics.serial.status=dependency_missing
  -> rgb candidates: scan limited OpenCV DirectShow indices, release after probe, augment Windows FriendlyName/PnP/VID/PID/USB serial when available; inferred index mapping is not promoted to stableId
  -> dvp2 candidates: DVP2 SDK enum only, stableId prefers serial/user id; host adapter fields are filled only when Windows IPv4 network matching is reliable
  -> POST /api/devices/bind
  -> DeviceRegistry saves role binding to runtime/hardware_profile.json
  -> DeviceManager updates selected RGB index or DVP2 serial in adapter config without opening devices

app.js runDeviceTest()/confirmCalibrationCheck()
  -> POST /api/device-preparation
  -> SessionState.update_device_preparation()
  -> SessionState.devicePrepared = all(connect, motor, light)
  -> SessionState.trueCapturePrepared = DeviceManager.capture_readiness(current TrueCapturePlan).ready
```

串口连接、STM32 PING、风扇、LED3、推杆、滤光轮相对移动/STOP、人工 SET_ORIGIN、升降门/输出状态查询和急停已有 Web API。P1B-7.5A.1 后 `DeviceManager` 默认把同一个 `SerialService` 包成 current-firmware AA55 adapter；P1B-7.5A.2 后 HTTP handler 新增 `POST /api/device/fan`、`/api/device/led`、`/api/device/actuator`、`/api/device/wheel/move-relative`、`/api/device/wheel/stop`、`/api/device/wheel/set-origin`，并全部通过 `DeviceManager -> Stm32ControllerAdapter -> SerialService` 执行。`CURRENT_STM32_FIRMWARE_PROFILE` 固化 `MOVE_ABS=0x01`、`MOVE_REL=0x02`、`STOP=0x03`、`SET_POS_PID=0x04`、`SET_VEL_PID=0x05`、`SET_PROFILE=0x06`、`SET_CONFIG=0x07`、`QUERY_STATUS=0x08`、`SET_ORIGIN=0x09`、`RESET=0x0F`、`FAN_SET=0x10`、`DOOR_SET=0x11`、`LED_SET=0x12`、`RSP_ACK=0x80`、`RSP_STATUS=0x81`、`RSP_INFO=0x82`。CRC16-CCITT-FALSE 覆盖 `CMD+PLEN+PAYLOAD`，ACK 必须按 echo cmd 匹配，QUERY_STATUS 只等待 STATUS 不等待 ACK，STATUS/INFO/ASCII ready noise 可交错并进入最新 cache。

STATUS cache 带 `revision` 和 `statusReceivedMonotonic`。用于输出验证和机械完成判定时，`query_status(require_fresh=True)` 不允许在超时时返回旧缓存。滤光轮 mapping 为 16 slots、1600 ppr、22.5°/slot、默认 10 rpm，主机 MOVE_REL/MOVE_ABS 发送 float32 LE degrees + rpm，不发送 pulses；Web 手动移动只发送 direction + 正 slots，后端映射 counterclockwise=正 slot、clockwise=负 slot，默认 `maxRetries=0`。完成判定必须看到 post-command fresh STATUS，并确认 target/position 变化、final position 到达 expected target、motor state idle/done 且 errorCode=0；ACK OK 但 position/target 未变化返回 `motion_not_started` 并 STOP，不盲目重复相对运动。PPR mismatch 只报告 `filterWheelPprMismatch` 并要求人工确认，不自动 SET_CONFIG。FAN_SET 发送 duty 0/100 并用 fresh STATUS 回读验证；LED_SET 只把 UI 暴露为 LED3 on/off，对应 mask 0x04/0x00；DOOR_SET 的 ACK 只代表推杆 command accepted，不代表 endpoint reached，后端用 token/generation 定时 STOP，避免旧 timer 停止新动作；SET_ORIGIN 只表示人工对准后建立逻辑 0°，不是 automatic HOME sensor；STATUS position 是逻辑位置，不等于 CL57C physical encoder feedback。当前 firmware 不支持远程 Fault Clear，UI 禁用并提示 unsupported。Emergency Stop 继续执行 filter wheel STOP、door STOP、LED off、fan on。

`DeviceManager.self_test()` 为非破坏通信自检，只执行 PING 和 fresh STATUS，不开启风扇、不移动滤光轮。`DeviceManager.self_test()` 的 `checks.rgbCamera` 来自 `RgbUvcCamera` 的 OpenCV/DirectShow probe；当前电脑验证默认配置为 `device_index=1`、`MJPG`、`3840x2160`、`25fps`，状态会同时暴露请求值和驱动实际返回值。RGB probe 成功后会释放 `VideoCapture` 句柄，此时 `opened=false`、`streaming=false`，但 `detected=true`、`available=true` 会保留到下一次失败 probe 或配置设备索引变化。P1B-3 的 `run_rgb_capture()` 可在 RGB 安全准备链后通过 `CameraManager.capture_rgb_frame()` 采集并保存一张正式 RGB PNG，但未插 RGB 相机或设备被占用时仍必须失败。`checks.multispectralCamera` 来自 `Dvp2MonoCamera` 的 SDK/枚举/probe 状态；目标设备为 DO3THINK/度申 GigE/RJ45 黑白相机，未安装/未找到 `DVPCamera64.dll` 时为 `sdk_missing`，只有 probe/open 成功才可 passed；不能由普通网卡 link 推断为相机已连接。若 DVP2 已枚举目标但无法打开，UI 应提示关闭 BasedCam3 或其他相机程序。P1B-8 后 `/api/capture/start` 通过 `DeviceManager.capture_readiness()` 统一检查当前 `TrueCapturePlan`，再调用 `CaptureCoordinator.run_true_capture()` 复用 RGB、DVP2、多波段、Dark/White 和 MultiView 受保护方法；单视角不要求 SampleStage，多视角仍因真实样品台协议未知被阻断。标定仍需要 operator confirmation 或 existing `calibrationId`，因为 Dark/White 软件路径不等于物理遮光和标准白板已现场验收。`/api/new-sample` 和 Offline/Demo `/api/complete-capture` 仍会通过 `require_device_preparation()` 阻止未完成当前离线设备准备时开始样品流程。

P1B-7.5B 新增独立 SampleStage API：

```text
GET  /api/device/sample-stage/status
POST /api/device/sample-stage/home
POST /api/device/sample-stage/move-absolute
POST /api/device/sample-stage/move-relative
POST /api/device/sample-stage/stop
```

这些 API 的调用链固定为 `backend_server.py -> DeviceManager -> SampleStage adapter`，禁止 HTTP handler 直接写串口，禁止复用 `Stm32ControllerAdapter` 的 filter-wheel motor。默认 adapter 是 `UnimplementedSampleStage`，状态中明确返回 `SAMPLE_STAGE_PROTOCOL_UNKNOWN`、`protocolKnown=false`、`positionFeedbackSupported=false`、`homeSupported=null`、`automaticHoming=null`。在真实控制器资料补齐前，HOME、move 和 STOP 调试命令返回 unsupported，不作为硬件失败或 PASS 证据。

设备发现层只建立候选和角色绑定，不改变 `trueCapturePrepared`。STM32 当前 discovery 只能设置 `metadata.protocolMatched=true/false`，并保留 `deviceType/deviceId/firmwareVersion/capabilities=None`，因为固件尚无身份命令。RGB 候选会尝试读取 Windows FriendlyName/PnP InstanceId/VID/PID/USB serial；只有 exact/verified 映射时才生成 stableId，若只是按顺序把 Windows 设备和 DirectShow index 对上，则只记录 `mappingConfidence=inferred`、`potentialStableId` 和 `lastDeviceIndex`。DVP2 候选以 SDK 枚举到的 serial/original serial/user id/friendly name 作为匹配来源。自动匹配规则优先 stableId，其次才用已验证的 last known location；找不到旧设备时保持 unbound，不自动使用列表第一项。

### Camera Service 数据边界

```text
CameraManager
  -> RgbUvcCamera
     -> cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
        or cv2.VideoCapture(device_index + cv2.CAP_DSHOW) for the same logical index
     -> set FOURCC MJPG, width 3840, height 2160, fps 25 by RgbCameraConfig
     -> read actual width/height/fps/fourcc and probe exposure/gain/white balance capability
     -> probe_available(): read one frame, persist detected/available, then release capture handle
     -> capture_frame()
     -> CameraFrame(data=<RGB uint8 HxWx3>, color_space="RGB")
     -> CameraManager.capture_rgb_frame()
     -> production RGB CameraFrame + requested/actual/device metadata
     -> RGB scientific capture saves lossless PNG in CaptureCoordinator
     -> CameraManager.rgb_preview_jpeg()
     -> low-latency browser JPEG preview from latest encoded cache, default 960x540, up to 12 fps

CameraManager
  -> Dvp2MonoCamera
     -> find_dvp2_sdk(configured dir / DVP2_SDK_DIR / D:\Netease\DVP2 SDK CN / Program Files candidates)
     -> Dvp2Binding(ctypes.CDLL(DVPCamera64.dll))
     -> dvpRefresh() + dvpEnum()
     -> select configured serial/user_id GP23400004963 before index
     -> dvpOpenByUserId() or dvpOpenByName()
     -> get ROI / exposure / analog gain / trigger / frame count
     -> dvpStart() + dvpGetFrame()
     -> CameraFrame(data=<MONO uint8 or uint16 HxW>, color_space="MONO")
  -> CameraManager.multispectral_preview_jpeg()
  -> browser JPEG preview only; raw frame dtype is preserved
  -> P1B-5.4+ low-latency preview: one background acquisition thread keeps latest raw frame and latest encoded JPEG caches; HTTP preview requests return the latest JPEG cache and do not enqueue DVP2 get_frame or per-request JPEG work
  -> CameraManager.capture_multispectral_frame()
  -> production MONO CameraFrame + requested/actual/device metadata
  -> preview running: reuse stream; preview stopped: temporary open/start/capture/stop/close

Current DVP2 verification:
  -> ctypes DLL load: passed
  -> dvpRefresh/dvpEnum: passed in user manual verification; target MGV231M-H2 at 169.254.25.110
  -> dvpOpenByUserId/open by target: passed in user manual verification after BasedCam3 fully exited
  -> parameter query / start / frame / 30-frame stability / PNG save: passed in user manual verification, Mono8 uint8 2048x1200
  -> Codex 2026-09-04 rerun in current environment: dvpRefresh/dvpEnum returned 0, so web visual preview not reverified in this run
```

Camera adapter 只负责设备状态和帧，不负责 Sample Folder、文件命名或 `metadata.json.image_directories`。`CameraManager` 是 RGB 和 DVP2 相机的单实例所有者：self-test、probe、preview、参数应用和正式单帧取帧都共用对应 adapter。RGB 与 DVP2 预览均使用后台 worker 持续取最新帧并覆盖 latest raw/latest encoded JPEG cache，HTTP preview-frame 请求只读缓存并返回 JPEG；前端在每次 fetch 完成后再调度下一帧，避免请求堆积。RGB 预览运行中 `capture_rgb_frame()` 复用已打开的句柄并直接取正式 RGB `CameraFrame`，预览停止时则临时打开、取帧后关闭。DVP2 预览运行中 `capture_multispectral_frame()` 复用当前 stream，但它仍直接取正式 raw `CameraFrame`，不读取 preview JPEG，也不使用 latest-frame cache 冒充 scientific capture；预览停止时由 `Dvp2MonoCamera.capture_frame()` 触发临时 open/start/get frame，随后由 manager 确定性 stop/close。预览 worker 与正式 capture 共享同一 adapter identity 和 capture lock，避免重复 open 同一相机；stop preview 会 set stop event、join thread、stop stream、close adapter 并清空 cache。真实采集保存路径由 `CaptureCoordinator.run_rgb_capture()` / `run_multispectral_capture()` / `run_multispectral_sequence()` / `run_dark_reference_capture()` / `run_white_reference_capture()` / `run_sample_multiview_capture()` 根据目录名和文件名写入样品目录；正式 scientific image 文件统一保存 lossless PNG，正式 metadata 不引用 `.jpg/.jpeg`；旧 `depthDir` 仅用于历史 API 兼容或真正 RGB-D depth 路径。

### 相机设置 API

```text
GET /api/camera/status
  -> DeviceManager.camera_manager.status()
  -> cameras.rgb / cameras.multispectral / preview.rgb / preview.multispectral

POST /api/camera/rgb/probe
  -> CameraManager.probe_rgb()
  -> RgbUvcCamera.probe_available()
  -> open configured device_index only, read one frame, close handle
  -> status.detected/status.available remain true after close when probe succeeds

POST /api/camera/rgb/apply-settings
  -> CameraManager.apply_rgb_settings()
  -> RgbCameraConfig.from_dict(payload)
  -> restart if device_index / width / height / fps / fourcc changed
  -> RgbUvcCamera.apply_config()
  -> status.requested + status.actual + status.capabilities

POST /api/camera/rgb/preview/start
  -> CameraManager.start_rgb_preview({width: 960, height: 540, fps: 12})
  -> RgbUvcCamera.start_stream()

GET /api/camera/rgb/preview-frame
  -> CameraManager.rgb_preview_jpeg()
  -> read latest encoded JPEG cache from the RGB preview worker
  -> response headers include frameId, sourceTimestamp/sourceAgeMs, captureDurationMs, resizeDurationMs, jpegEncodeDurationMs, serverTotalMs, measuredPreviewFps, droppedFrames and encoder

POST /api/camera/rgb/preview/stop
  -> CameraManager.stop_rgb_preview()
  -> stop_stream() + close()

POST /api/camera/multispectral/probe
  -> CameraManager.probe_multispectral()
  -> Dvp2MonoCamera.probe_available()
  -> subprocess DVP2 enum/open probe with timeout, so vendor DLL open cannot freeze the main backend

POST /api/camera/multispectral/apply-settings
  -> CameraManager.apply_multispectral_settings()
  -> Dvp2MonoCamera.set_exposure()/set_gain()
  -> DVP2 SDK write + actual readback

POST /api/camera/multispectral/preview/start
  -> CameraManager.start_multispectral_preview({width: 960, height: 540, fps: 12, lowLatency: true})
  -> probe first; only then attempt Dvp2MonoCamera.start_stream()
  -> start one latest-frame background acquisition thread

GET /api/camera/multispectral/preview-frame
  -> CameraManager.multispectral_preview_jpeg()
  -> read latest encoded JPEG cache, not one DVP2 get_frame or JPEG encode per HTTP request
  -> normalize to 8-bit JPEG only inside preview worker for browser preview; prefer OpenCV resize/imencode when available, PIL fallback otherwise
  -> response headers include source dtype, pixel format, frame min/max/mean, frameId, sourceTimestamp/sourceAgeMs, captureDurationMs, resizeDurationMs, jpegEncodeDurationMs, serverTotalMs, measuredPreviewFps, droppedFrames and encoder

POST /api/camera/multispectral/preview/stop
  -> CameraManager.stop_multispectral_preview()
  -> stop latest-frame worker + join + stop_stream() + close()
```

预览分辨率和 JPEG 编码只用于浏览器观察，不改变正式 RGB 相机请求配置，也不作为 DVP2 正式保存输入；当前 RGB 正式配置仍为 `3840x2160`、`MJPG`、`25fps`，DVP2 正式单帧、多波段 sample sequence、Dark/White calibration sequence、Sample MultiView 和 P1B-8 True Capture 保存直接使用 `CameraFrame.data` 并写 lossless PNG。`trueCapturePrepared` 由当前 `TrueCapturePlan` readiness 计算，preview/apply settings 成功不能单独放行真实采集。

### Calibration Capture API

```text
POST /api/capture/calibration/dark
  -> CaptureCoordinator.run_dark_reference_capture()
  -> hardware_precheck / door_close / fan_on
  -> dark_lighting_shutdown / lighting_off_verify
  -> operator_confirmation:dark
  -> shared multispectral band sequence
  -> save raw calibration/dark/band_XX_<bandId>.png
  -> write calibration/calibration_set_<calibrationId>.json

POST /api/capture/calibration/white
  -> CaptureCoordinator.run_white_reference_capture()
  -> hardware_precheck / door_close / fan_on
  -> operator_confirmation:white
  -> multispectral_light_prepare / capture_safety_check
  -> shared multispectral band sequence
  -> save raw calibration/white/band_XX_<bandId>.png
  -> write calibration/calibration_set_<calibrationId>.json
```

这两个接口是受保护的 calibration 开发/现场验收入口，也可被 P1B-8 `run_true_capture()` 在 `calibrationMode=capture_new` 时复用。调用方必须传入或已有 `outputDir`，并显式传递 `operatorConfirmed=true` 或由 UI confirmation 注入确认。失败、取消和超时会返回 coordinator snapshot；HTTP 不把 failed/cancelled 包装成成功。

### Sample MultiView Capture API

```text
POST /api/capture/sample-multiview
  -> CaptureCoordinator.run_sample_multiview_capture()
  -> build/reuse rotation_plan.build_capture_rotation_plan()
  -> hardware_precheck / calibration_check / door_close / fan_on
  -> optional sample_stage_home
  -> per View:
       view_begin
       sample_stage_move
       sample_stage_position_verify
       sample_stage_settle
       rgb_light_prepare / capture_safety_check / RGB raw PNG save / lighting shutdown
       multispectral_light_prepare / capture_safety_check
       shared P1B-5 multispectral band sequence
       view_complete_verify
  -> optional return_home
  -> lighting_shutdown
  -> write metadata.json and views.json
```

这是受保护的软件编排/现场验收入口，不是完整用户主采集入口。`sampleStageMode=hardware` 只有注入真实 sample stage adapter 且能完成 HOME、move、stable、position readback 时才可通过；没有 adapter 时返回 `hardware_not_implemented`，不会自动 fallback simulation。`sampleStageMode=simulation` 仅用于 unittest 和离线软件编排验证。MultiView 使用同一个 `sample_id` 和同一个 sample 级 `calibrationId`，不为每个 View 重拍 Dark/White。每个 View 的 RGB 写入 `views/<viewId>/rgb/rgb_<viewId>.png`，多光谱写入 `views/<viewId>/multispectral/band_XX_<bandId>.png`；旧 `rgb/` 和 `multispectral/` 单层目录仍作为已有流程兼容路径。

### CaptureCoordinator 骨架

P1B 新增 `CaptureCoordinator`，作为后续真实采集流程的唯一编排入口。当前它建立架构边界：`CaptureState` 覆盖 `idle/preparing/capturing/finalizing/completed/cancelling/cancelled/failed`，`CaptureStep` 记录步骤 `id/name/status/startedAt/finishedAt/durationMs/timeoutMs/error/result`，`CaptureRun` 记录 `captureId/sampleId/mode/state/currentStep/progress/error/cancelRequested/outputDir/steps/metadata`。步骤通过 `CaptureStepPlan` 注入动作，便于分阶段接入硬件准备、RGB、DVP2、滤光轮、样品旋转和文件保存。

P1B-2 中，`run_preparation(mode="rgb"|"multispectral")` 已通过 `HardwareController` 高层 API 执行 STM32 安全准备链：`hardware_precheck` 执行 PING 和故障码检查，`door_close` 下发关门并在有反馈时确认 `DoorState.CLOSED`，`fan_on` 开启风扇并通过输出状态确认，`rgb_light_prepare` 先关钨灯再开启 RGB LED 并调用 `ensure_rgb_capture_ready()`，`multispectral_light_prepare` 先关 RGB LED 再开启钨灯并调用 `ensure_multispectral_capture_ready()`，`capture_safety_check` 再次调用对应 interlock，`lighting_shutdown` 关闭采集光源。Coordinator 不直接调用 `SerialService.send_command()`，也不复制硬件 interlock 细节。

P1B-3 中，`run_rgb_capture()` 复用 RGB 准备链，并在 `capture_safety_check` 和 `lighting_shutdown` 之间插入 `rgb_capture`：通过 `CameraManager.capture_rgb_frame()` 获取 RGB `uint8` H×W×3 帧，校验非空、形状、dtype 和 RGB 色彩顺序，按 `<rgbDirName>/rgb_view_000.png` 默认命名写入 PNG。写入前用独占创建目标名防止覆盖，先保存临时 PNG，再替换为最终文件，并在记录 metadata 前确认最终文件存在且大小大于 0。metadata `frames` 记录相对路径、绝对路径、宽高、通道、dtype、pixel order、source pixel order、设备信息、requested/actual settings、是否复用预览句柄等。取消请求在取帧前和保存前检查；若帧已保存后才出现取消，metadata 保留已保存事实。

P1B-4 中，`run_multispectral_capture()` 复用多光谱准备链，并在 `capture_safety_check` 和 `lighting_shutdown` 之间插入 `multispectral_capture`：通过 `CameraManager.capture_multispectral_frame()` 获取 DVP2 `MONO` H×W raw 帧，校验非空、二维、单通道、dtype 只能为 `uint8` 或 `uint16`，按 `<multispectralDirName>/multispectral_frame_000.png` 默认命名写入 PNG。写入前拒绝覆盖已有目标文件，先保存临时 PNG，读回验证尺寸、单通道和 dtype 后再替换最终文件，并再次读回验证。metadata `frames` 记录相对路径、绝对路径、宽高、dtype、PixelFormat、曝光/增益、frame stats、设备信息、requested/actual settings、是否复用预览 stream，并固定记录 `wavelengthNm=null`、`bandAssignment=unassigned`、`filterWheelSynchronized=false`。

P1B-5 中，`MultispectralBandPlan` 记录 `bandId`、滤光轮目标位置、中心波长、带宽、enabled、按波段曝光和增益；`MultispectralCapturePlan` 记录 band 列表、filter config source/version、developmentConfig 和 settlingMs。`run_multispectral_sequence()` 会从显式 plan 或 `quality_algorithm.filters.load_filter_config()` 构建计划，只采集 enabled bands，并在 `lighting_shutdown` 之前插入动态步骤：`filter_wheel_home`、每个 band 的 `filter_wheel_move:<bandId>`、`filter_wheel_position_verify:<bandId>`、`filter_wheel_settle:<bandId>`、`band_camera_settings:<bandId>`、`multispectral_capture:<bandId>`。滤光轮控制只通过 `HardwareController.wheel_home()`、`wheel_move_relative()`、`get_wheel_status()`；若 HOME 后位置未知、移动后位置不匹配、相机设置/取帧/保存失败、取消或超时，则立即失败或取消并进入 `safe_stop()`，后续 band 不再采集。已保存 band 不回滚，metadata 标记 `partialCapture`、`failedBand`、`pendingBands` 或 `cancelled`。

P1B-5 的每个 band PNG 仍复用 P1B-4 raw mono 保存边界，默认文件名为 `<multispectralDirName>/band_XX_<bandId>.png`，保存前后验证 `uint8/uint16` 单通道位深和尺寸，不使用预览 JPEG 或显示归一化。metadata `bands` 记录完整 band plan，`multispectralSequence` 记录 enabled/disabled/completed/pending/failed bands、filterWheel 状态、settlingMs、filter config 来源和 developmentConfig；每个 frame 记录 `bandId`、`bandIndex`、`wavelengthNm`、`bandwidthNm`、`bandAssignment=<bandId>`、`filterWheelSynchronized=true`、`captureType=sample` 和确认后的滤光轮位置。

P1B-6 中，sample/dark/white 共用同一个 `_multispectral_band_sequence_steps()`，也就是同一套 filter wheel HOME、move、position verify、settling、band camera settings、raw DVP2 capture/save/verify 和 partial capture 处理。差异只在前置阶段：Dark 调用 `dark_lighting_shutdown` 与 `lighting_off_verify`，不调用 `multispectral_light_prepare`；White 先要求 `operator_confirmation:white`，再调用 `multispectral_light_prepare` 和 `capture_safety_check`。Dark 与 White 默认保存到 `calibration/dark/band_XX_<bandId>.png` 和 `calibration/white/band_XX_<bandId>.png`，metadata 明确 `captureType=dark|white`，记录 requested/actual exposure/gain、min/max/mean/std、Dark leakage 诊断、White saturation 与 uniformity 诊断。`CalibrationSet` 记录 `calibrationId`、camera identity、filter config、bands、dark/white frames、completed/missing bands、`calibrationComplete` 和 `sameBandSettingsMatched`；只有所有 enabled bands 同时具备 verified dark 和 white frame 时才 complete。`validate_calibration_compatibility()` 比较 camera stable identity、filter config/version、enabled band IDs、wavelength mapping、width/height、dtype、PixelFormat、exposure 和 gain，返回 `compatible`、`warning` 或 `incompatible`，但不计算 reflectance。

P1B-7 中，`SampleViewPlan` 只描述样品台视角，`MultispectralBandPlan` 只描述滤光轮波段，两者不共享 position/angle/motorState。`run_sample_multiview_capture()` 先复用 `rotation_plan.py` 生成或接收 view 列表，再逐 View 执行 sample stage move/readback/settling、RGB capture 和 P1B-5 multispectral band sequence。每个 View 记录 `sampleRotation` 与 `filterWheel` 两个独立 metadata 节点，只有位置确认、RGB 保存和所有 enabled bands 保存都成功时 `viewComplete=true`。某个 View 失败或取消后不开始后续 View，保留已保存数据，记录 `completedViews`、`failedView`、`pendingViews`、`partialCapture` 和 `safe_stop()` 状态。return home 失败只记录 `homeStatus`，不删除前面已保存科学数据。

P1B-8 新增 `TrueCapturePlan` 与 `CaptureCoordinator.run_true_capture()`。该方法不重写驱动，按 plan 复用 `run_dark_reference_capture()`、`run_white_reference_capture()`、`run_sample_multiview_capture()` 或 `run_rgb_capture()`：`captureMode=single_view` 强制使用单视角 `sample_rotation.enabled=false`，因此不要求真实 SampleStage；`captureMode=multi_view` 仍进入 SampleStage hardware gate，当前默认因 `SAMPLE_STAGE_PROTOCOL_UNKNOWN` 在 readiness/start 阶段被阻止。失败、取消和超时都会进入 `safe_stop()`，它通过注入的 callback、SampleStage、硬件控制器或 `DeviceManager.controller.safe_stop()` 尽力关闭输出/停止运动。metadata 只记录真实步骤和已保存帧；preview JPEG/latest-frame cache 不进入正式 capture metadata。

### True Capture API

```text
GET  /api/capture/readiness
POST /api/capture/start
POST /api/capture/cancel
GET  /api/capture/status
```

`/api/capture/start` 是 True Hardware Capture 的正式启动入口。payload 支持 `captureMode=single_view|multi_view`、`calibrationMode=existing|capture_new|none`、`calibrationId`、`bandPlan`、`rotationPlan/sampleRotation`、`rgbDirName`、`multispectralDirName`、`returnHome` 和 operator confirmation。后端先通过 `DeviceManager.capture_readiness()` 计算 `ready/blockingReasons/warnings/capabilities`，再调用 `CaptureCoordinator.run_true_capture()`。`trueCapturePrepared` 表示当前 capture plan 是否满足软件执行条件；`HARDWARE_ACCEPTANCE_NOT_PASSED` warning 明确说明这不等于 production release PASS。

`/api/complete-capture` 只保留 Offline/Demo 边界，默认继续调用 `create_offline_capture_dataset()` 生成开发验证 PNG；显式 true hardware 模式会返回 `TRUE_CAPTURE_USES_CAPTURE_START`，禁止把 offline dataset 当成真实采集成功。

### 样品创建

```text
app.js createNewSample()
  -> requireDevicePreparation()
  -> buildCaptureRotationPlan() 前端预览
  -> POST /api/new-sample
  -> backend_server.require_device_preparation()
  -> backend_server.handle_new_sample()
  -> rotation_plan.build_capture_rotation_plan()
  -> create_unique_sample_folder()
  -> SessionState.create_sample()
  -> ensure_sample_capture_folder()
```

输出：

```text
<save_root>/<YYYYMMDD_HHMMSS>_<sample_name>/
  <rgbDirName>/              # 默认 rgb，可在保存前设置
  <multispectralDirName>/    # 默认 multispectral，可在保存前设置
  views/<viewId>/rgb/         # P1B-7 受保护 MultiView 正式 RGB 帧
  views/<viewId>/multispectral/ # P1B-7 受保护 MultiView 多波段 raw 帧
  calibration/dark/
  calibration/white/
  metadata.json
  views.json
```

`metadata.json.image_directories` 保存本次实际使用的 RGB 与多光谱子目录名。`sample_rotation` 是样品台多视角计划；`filter_wheel_rotation` 是滤光片转轮波段切换说明。两者控制域独立，不能混用。

### 路径选择

```text
Main UI / Model Studio readonly path display
  -> GET /api/select-folder?purpose=<save|sample|model-studio-source|model-studio-sample>
  -> select_directory_dialog()
  -> validate_folder_path()
  -> {path, pathStatus}

Manual sample folder selection
  -> GET /api/select-folder?purpose=sample
  -> GET /api/inspect-image-folders?parentDir=<selected>
  -> user selects RGB + multispectral direct children
  -> GET /api/sample-folder?datasetDir=<parent>&colorDir=<rgbDirName>&multispectralDirName=<multispectralDirName>&strictImageDirs=1

Model Studio labels.csv
  -> GET /api/select-file?purpose=labels-csv
  -> select_file_dialog()
  -> validate_file_path()
  -> {path, pathStatus}
```

取消系统选择器时返回 `cancelled=true`，前端保留原路径。主程序保留旧 `/api/select-dataset`、`/api/select-save-root` 作为兼容入口，但新界面优先使用通用路径选择 API。

### 离线采集完成

```text
app.js completeCurrentCapture()
  -> POST /api/complete-capture
  -> rotation_plan.build_capture_rotation_plan()
  -> create_offline_capture_dataset()
  -> rotation_plan.mark_plan_completed()
  -> SessionState.current_capture_dir = capture_dir
  -> SessionState.analysis_data_dir = capture_dir
  -> SessionState.capture_rotation_plan = sample_rotation
```

当前写入模拟文件：

```text
<rgbDirName>/rgb_001.png ... rgb_003.png
<multispectralDirName>/450.png, 560.png, 670.png
calibration/dark/dark_001.png ...
calibration/white/white_001.png ...
metadata.json
```

启用样品多角度旋转拍摄时，为兼容当前非递归图片读取，仍保持单层目录：

```text
<rgbDirName>/rgb_view_000.png
<rgbDirName>/rgb_view_045.png
<multispectralDirName>/view000_450.png
<multispectralDirName>/view000_560.png
<multispectralDirName>/view045_450.png
...
views.json
metadata.json
```

每个 View 记录 `view_id`、`sample_id`、`logical_angle_deg`、`mechanical_angle_deg`、`direction`、`capture_order`、`rgb_files`、`multispectral_files`、`closure_view`。当前样品台回 Home 是离线模拟状态，真实硬件接入后应替换为独立的 sample stage 控制接口。

注意：未启用多角度时仍保留旧离线输出。启用多角度时暗/白文件按波长补充 `dark_<band>.png`、`white_<band>.png`；完整真实校准仍需真实采集规范。

### 目录检查和图片预览

```text
GET /api/sample-folder
  -> pointcloud_service.inspect_sample_folder()
  -> quality_algorithm.spectral_features.inspect_sample_structure()

GET /api/dataset-images
  -> resolve_image_analysis_dirs()
  -> list_images()
  -> /api/local-image?path=...
```

输出数据完整性、预期波段、已有波段、缺失波段、校准状态、坏图列表。

### 形态分析

```text
POST /api/analyze-shape
  -> JobStore.create()
  -> background thread
  -> pointcloud_service.analyze_rgbd_dataset()
```

`/api/analyze-shape` 只依赖请求中的 `datasetDir` 和目录结构检查，不强制要求当前样品会话；因此用户可直接选择本地已有样品文件夹进行形态分析。

优先路径：

```text
sample folder
  -> resolve rgb + multispectral
  -> read first RGB
  -> build_rgb_subject_mask()
  -> measure_rgb_frame()
  -> analyze_surface_texture()
  -> analyze_spectral_folder()
  -> optional cached PLY metrics
  -> result dict + preview images
```

输出到 UI：

- `areaPixels`
- `diameterPx`
- `heightPx`
- `perimeterPx`
- `bloomCoveragePercent`
- `colorUniformity`
- `spectralStats`
- 可选 PLY 点云指标

### 光谱特征和预测

模型普通/高级展示：

```text
app.js loadQualityModels()
  -> GET /api/quality-models?fruitType=<fruit>&variety=<variety>
  -> ModelStudioService.model_catalog()
  -> compatible + defaults
  -> app.js renderModelOverview()
  -> 普通模式显示 SSC/TA/pH 默认模型、通用模型或缺失模型
  -> 高级模式显示原有 model select
```

该展示层只复用现有 Published/Default/generic 逻辑，不发布模型、不替换模型，也不生成假预测值。

```text
POST /api/predict-ssc 或 /api/predict-acid
  -> build_quality_session()
  -> quality_prediction.build_sample_session()
  -> predict_ssc()/predict_ta()/predict_ph()
  -> _select_registry_model()
  -> load_model_bundle()
  -> extract_feature_record()
  -> predict_feature_record()
  -> PredictionResult.to_dict()
```

`extract_feature_record()`：

```text
rgb first image
  -> RGB ROI mask
multispectral/<wavelength>.png
  -> dark/white reflectance correction if matching files exist
  -> otherwise normalize_uncalibrated() when allowed
  -> ROI mean per enabled band
  -> FeatureRecord(wavelengths, features, calibrated, warnings)
```

`predict_feature_record()`：

```text
FeatureRecord
  -> validate wavelengths and calibration_required
  -> transform RAW/SNV/MSC
  -> sklearn model.predict()
```

## Model Studio 架构

SQLite 数据库路径：

```text
host_software/static_ui_prototype_bin/model_studio/database/model_studio.sqlite
```

Dataset 本地托管仓库：

```text
host_software/static_ui_prototype_bin/model_studio_data/
└─ datasets/
   └─ <dataset_id>/
      ├─ samples/
      │  └─ <sample_id>/
      │     ├─ rgb/
      │     ├─ multispectral/
      │     ├─ calibration/
      │     └─ metadata.json
      └─ labels.csv
```

主要表：

- `datasets`：`dataset_id`、`dataset_name`、`fruit_type`、`variety`、`storage_path`、`local_path`、`import_source_path`、`dirty`、`latest_version_id`、`archived`、`updated_at`。
- `dataset_versions`：`sample_ids`、`sample_snapshot_json`、`label_snapshot_json`、`snapshot_hash`，用于冻结版本样品和标签。
- `samples`：`sample_id`、`sample_name`、`source_path`、`local_path`、`storage_path`、RGB/多光谱/校准计数、标签镜像、状态字段。
- `labels`：SQLite 标签权威来源，保存 `sample_id`、`ssc`、`ta`、`ph`、`updated_at`。
- `training_experiments`
- `jobs`
- `models`：Candidate / Validated / Published / Default / Archived 生命周期，记录 Dataset Version、Experiment、Run、parent model、metrics、tags、notes 和受管模型文件路径。
- `operation_logs`：记录 `dataset.archive`、`dataset.delete`、`model.archive`、`model.delete` 等可追踪操作；Permanent Delete 后仍保留 resource id/name 文本。

训练文件流：

```text
External Sample Folder
  -> validate_sample_folder()
  -> import_samples()
  -> COPY to model_studio_data/datasets/<dataset_id>/samples/<sample_id>
  -> samples.source_path = external source
  -> samples.local_path/storage_path = managed local copy

Sample detail form
  -> save_sample_label()
  -> labels table
  -> samples.ssc/ta/ph mirror
  -> model_studio_data/datasets/<dataset_id>/labels.csv
  -> datasets.dirty = 1

Sample include/exclude/delete
  -> update_sample_status()
  -> Excluded samples stay in managed storage and do not enter new Dataset Version
  -> sample_references()
  -> referenced samples are blocked from permanent delete
  -> delete_sample()
  -> delete labels + sample row
  -> optional delete managed local copy under Dataset samples/
  -> never delete samples.source_path

Dataset archive/delete
  -> archive_dataset()
  -> keep Dataset/Samples/Versions/Experiments/Jobs/Models/Lineage/files
  -> hide archived datasets by default list filter
  -> dataset_references()
  -> block permanent delete when Published/Default/Production models reference dataset
  -> delete_dataset_permanently()
  -> cascade delete non-production experiment records and Candidate/Validated/Archived models
  -> delete only managed model_studio_data/model_studio/artifacts/model_studio/models/trained_models/published paths
  -> never delete external import_source_path/source_path

Dataset local storage
  -> import_labels()
  -> create_dataset_version()
  -> sample_snapshot_json + label_snapshot_json
  -> generate_features()
  -> model_studio/artifacts/features/<dataset_version>_features.csv
  -> create_experiment()
  -> create_training_job()
  -> training.train_one()
  -> model_studio/models/candidates/<experiment>/<target>_<pre>_<model>/
  -> models.status = Candidate
  -> Model Compare shows Algorithm + Preprocessing variants
  -> publish_model()
  -> trained_models/published/<model_id>/
  -> optional setDefault
  -> trained_models/<target>/
  -> retrain_from_model(parent_model_id)
  -> new Experiment / Run / Model without overwriting old model
```

模型发布约束：

- Candidate 不会自动进入主程序。
- Published 可被主检测工作站手动选择，但 Default 才会作为对应 fruit_type/variety/target 的自动默认。
- Archived 保留数据库记录、模型文件和 lineage，但不进入 `/api/quality-models`，因此不参与主检测工作站自动或手动选择。
- Permanent Delete 必须先确认模型不是 Default，再同步删除 SQLite models 记录、受管 candidate/published artifacts；已作为 legacy `trained_models/<target>` 的 Default 不能直接删除。Published 非 Default 删除后不再进入 `/api/quality-models`。
- `trained_models/<target>/` 是 legacy/default fallback，不代表所有已发布模型。

Model Studio 一级 UI 结构：

```text
Dashboard -> Datasets -> Training -> Models -> Settings
```

Dataset 页面内部承载 Samples、Versions、Quality、Experiments。Training 页面按 Data / Configure / Train / Compare & Publish 展示；一个 Experiment 只对应一个 target，一个 Run 是一次 jobs 执行，一个 Model Variant 是 Algorithm + Preprocessing 的模型结果。Dataset Version 通过 `sample_snapshot_json` 和 `label_snapshot_json` 保持不可变，并可用 `dataset_version_diff()` 比较 added / removed-or-excluded / label-changed。

## 依赖关系

核心运行依赖：

- `numpy`
- `Pillow`
- `opencv-python`
- `scikit-learn`
- `joblib`

可选/运行时检查：

- `matplotlib`
- `scipy`
- `open3d`
- `cv2`

`FruitTasteAnalyzer.spec` 打包排除了 `matplotlib`、`scipy`、`pytest`、`sphinx`、`docutils`、`lxml`，所以点云预览会走 Pillow fallback 或受限路径。

## 当前真实数据目录状态

- `sample_data/README.md` 说明不再随程序内置 demo 图像目录。
- `trained_models/` 当前没有可用模型 bundle。
- `model_studio/database/model_studio.sqlite` 当前为空/待初始化。
- `outputs/` 下有过往验证输出，属于运行产物，不是正式样品数据。
