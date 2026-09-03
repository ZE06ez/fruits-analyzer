# Changelog

本文档只记录能从 Git 历史或当前代码确认的阶段。无法确认具体日期的内容标记为“历史版本，具体日期待确认”。

## 2026-09-03 RGB 相机探测状态修正

- 修改内容：新增 `/api/camera/rgb/probe`，让相机设置页“重新检测”执行真实 OpenCV DirectShow 打开、请求 MJPG/3840x2160/25fps、取一帧、释放句柄的 probe；`CameraStatus` 增加 `detected` 和 `opened`，并明确区分 `detected`、`available`、`opened`、`streaming`；`RgbUvcCamera` 记录最近一次真实 probe/capture 成功状态，`close()` 和预览停止不再清空 `detected/available`；同一配置 `device_index` 下兼容 `VideoCapture(index, CAP_DSHOW)` 和 `VideoCapture(index + CAP_DSHOW)` 两种 DirectShow 打开形式，不扫描或 fallback 到内置摄像头；`CameraManager` 统一 self-test、probe、preview 对同一个 RGB adapter 的状态更新；前端 `testRgbCamera` 改为调用 probe API，预览请求继续使用相对 URL，并将 `Failed to fetch` 解释为本地后端连接问题而不是硬件断开。
- 修改文件：
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/camera_service/base.py`
  - `host_software/static_ui_prototype_bin/camera_service/rgb_uvc.py`
  - `host_software/static_ui_prototype_bin/camera_service/manager.py`
  - `host_software/static_ui_prototype_bin/tests/test_camera_service.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `AGENTS.md`
- 为什么修改：手动 OpenCV 测试已确认当前 RGB 相机可用，但主程序把“句柄已释放”误显示为“未连接”；本次修正软件状态语义和 API 调用链，避免把已验证相机误判为断开。
- 是否影响原有功能：不修改 DVP2、多光谱、STM32、CaptureCoordinator、`CameraIntegrationRequired`、`trueCapturePrepared`、`create_offline_capture_dataset()` 或图像目录工作流；不放行正式真实采集。

## 2026-09-03 Camera Settings UX + RGB Preview

- 修改内容：重整主程序相机设置页，增加 RGB 彩色相机 / 多光谱黑白相机分段切换；RGB 页面分为设备连接、采集参数、图像参数、实时预览、requested/actual 应用结果、高级标定参数和标定状态；`fx/fy/cx/cy` 移到高级设置；“保存参数”拆分为“应用到相机”和“保存为默认配置”；新增 `/api/camera/status`、`/api/camera/rgb/apply-settings`、`/api/camera/rgb/preview/start`、`/api/camera/rgb/preview-frame`、`/api/camera/rgb/preview/stop`；`CameraManager` 用单实例和锁统一 RGB self-test、preview、参数应用，预览帧从真实 RGB 取帧后降采样为 960x540 JPEG；多光谱页改为 DO3THINK/度申 GigE/RJ45 + DVP2 待接入结构，PixelFormat/Exposure/Gain/Trigger 和波段曝光表均禁用/预留，不显示白平衡。
- 修改文件：
  - `host_software/static_ui_prototype_bin/index.html`
  - `host_software/static_ui_prototype_bin/styles.css`
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/camera_service/rgb_uvc.py`
  - `host_software/static_ui_prototype_bin/camera_service/manager.py`
  - `host_software/static_ui_prototype_bin/tests/test_camera_service.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `AGENTS.md`
- 为什么修改：把相机设置从开发调试表单整理成普通检测设备可理解的交互，并让 RGB 参数形成 UI -> Backend API -> CameraManager -> RgbUvcCamera -> actual 回读的真实闭环。
- 是否影响原有功能：不进入 P1B，不实现 CaptureCoordinator，不修改图像目录工作流，不伪造多光谱 DVP2 API，不放行 `/api/capture/start`；`trueCapturePrepared` 继续保持 false。

## 2026-09-02 P1A-RGB Hardware Finalize

- 修改内容：收尾已通过当前电脑实机验证的 RGB UVC 相机层；新增 `RgbCameraConfig` 配置层，默认记录 `device_index=1`、`MJPG`、`3840x2160`、`25fps`，但 adapter 内不硬编码设备索引；`RgbUvcCamera.open()` 按 DirectShow -> FOURCC -> 宽高 -> FPS 顺序请求参数，并在状态中分离 `requested`、`actual`、`capabilities`；`capture_frame()` 继续返回 RGB `uint8` H×W×3；手动测试脚本增加 width/height/fps/fourcc/exposure/gain/frames 参数、稳定性输出和能力探测输出；主 UI 相机设定默认值改为当前 RGB 实机验证参数；多光谱相机文档和状态改为 DO3THINK/度申 GigE/RJ45 + DVP2 边界，不把 Wi-Fi/普通网口 link 当成相机连接，不使用 OpenCV VideoCapture。
- 修改文件：
  - `host_software/static_ui_prototype_bin/index.html`
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/camera_service/__init__.py`
  - `host_software/static_ui_prototype_bin/camera_service/base.py`
  - `host_software/static_ui_prototype_bin/camera_service/config.py`
  - `host_software/static_ui_prototype_bin/camera_service/rgb_uvc.py`
  - `host_software/static_ui_prototype_bin/camera_service/dvp2_mono.py`
  - `host_software/static_ui_prototype_bin/camera_service/manager.py`
  - `host_software/static_ui_prototype_bin/manual_camera_test.py`
  - `host_software/static_ui_prototype_bin/tests/test_camera_service.py`
  - `host_software/static_ui_prototype_bin/tests/test_device_manager.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `AGENTS.md`
- 为什么修改：把“RGB 相机已经能在当前电脑真实打开和取帧”这件事固化进配置、状态、手动测试和文档，同时继续防止主流程误认为完整真实采集已就绪。
- 是否影响原有功能：不开发 P1B，不实现 CaptureCoordinator，不放行 `/api/capture/start`，不自动打开真实相机跑 unittest，不修改图像目录工作流，不提交 `camera/` 厂商资料或 SDK 二进制。

## 2026-09-02 P1A-1 Camera Service 基础层与 RGB UVC 适配

- 修改内容：新增独立 `camera_service` 包，定义统一相机状态、帧结构和异常；新增 `RgbUvcCamera`，通过 OpenCV `cv2.CAP_DSHOW` 适配 Windows UVC/DirectShow RGB 相机，`capture_frame()` 返回 RGB `uint8` H×W×3 numpy 帧；新增 `Dvp2MonoCamera`，只做 DVP2 SDK/DLL 发现和安全 unavailable/unsupported 状态，不猜测未确认的 DVP2 API；新增 `CameraManager` 并接入 `DeviceManager.status()`、`DeviceManager.self_test()` 和 `/api/status` 的 camera 状态；一键设备检查读取 CameraManager，RGB 未插入时为 `not_connected`，DVP2 缺 SDK 时为 `sdk_missing`；`trueCapturePrepared` 在 P1A-1 仍强制为 `false`，`CameraIntegrationRequired` 继续保护真实采集入口。
- 修改文件：
  - `host_software/static_ui_prototype_bin/camera_service/__init__.py`
  - `host_software/static_ui_prototype_bin/camera_service/base.py`
  - `host_software/static_ui_prototype_bin/camera_service/errors.py`
  - `host_software/static_ui_prototype_bin/camera_service/rgb_uvc.py`
  - `host_software/static_ui_prototype_bin/camera_service/dvp2_mono.py`
  - `host_software/static_ui_prototype_bin/camera_service/manager.py`
  - `host_software/static_ui_prototype_bin/manual_camera_test.py`
  - `host_software/static_ui_prototype_bin/device_manager.py`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/styles.css`
  - `host_software/static_ui_prototype_bin/tests/test_camera_service.py`
  - `host_software/static_ui_prototype_bin/tests/test_device_manager.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_data_flow.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
- 为什么修改：为真实相机接入建立可测试、可扩展的边界，同时把当前能真实实现的 RGB/UVC 路径与暂时只能发现 SDK 的 DVP2 路径严格分开。
- 是否影响原有功能：不修改图像目录工作流，不修改 `create_offline_capture_dataset()`，不接入 P1B CaptureCoordinator，不放行真实整套采集，不提交 `camera/` 厂商资料或 SDK 二进制。

## 2026-09-02 P0 普通检测流程状态与设备检查优化

- 修改内容：主界面顶栏新增统一“当前状态”，由前端集中函数按设备异常、正在执行任务、离线验证、分析任务、样品和设备准备状态派生；设备准备页新增“开始设备检查”普通入口，复用 `/api/device/status` 和 `/api/device/self-test`，并显示控制器、升降门、风扇、滤光轮、RGB 相机、多光谱相机、光源控制和标定状态；`DeviceManager.self_test()` 返回结构化 `checks`，相机明确为 `not_connected`，标定为 `manual_required`；样品采集页新增普通模式“检测模型”摘要，默认隐藏高级模型下拉框，点击“更换模型”后保留原有手动选择。
- 修改文件：
  - `host_software/static_ui_prototype_bin/index.html`
  - `host_software/static_ui_prototype_bin/styles.css`
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/device_manager.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_data_flow.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`
  - `host_software/static_ui_prototype_bin/tests/test_device_manager.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
- 为什么修改：降低普通检测用户的默认操作复杂度，把设备底层信息和模型细节收进详情/高级模式，同时继续保留科研调试能力。
- 是否影响原有功能：不修改图像目录保存/读取工作流，不接入或伪造相机 SDK，不修改 Production/Default 发布规则，不实现 P1/P2 的采集协调器、检测历史或报告系统。`/api/capture/start` 在相机服务接入前仍返回 `CameraIntegrationRequired`。

## 2026-09-01 图像保存与读取目录名可配置

- 修改内容：主程序保存父目录选择后新增“图像目录名称设置”模态框，允许设置 RGB 与多光谱子目录名；本次拍摄目录继续自动导入并优先使用 session/metadata 中的实际目录名；手动选择其他样品时新增父目录一级子目录扫描和子目录选择模态框，确认后再检查和分析。
- 修改文件：
  - `host_software/static_ui_prototype_bin/index.html`
  - `host_software/static_ui_prototype_bin/styles.css`
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/pointcloud_service.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_data_flow.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
- 为什么修改：避免把 RGB/多光谱目录名称固定为 `rgb` 和 `multispectral`，同时保留旧数据默认兼容，并让用户通过选择器而不是手动路径输入指定图像目录。
- 是否影响原有功能：默认目录名仍为 `rgb`/`multispectral`；原有旧数据仍可读取；本次拍摄自动导入保留；手动选择其他目录时需要确认一级子目录。

## 2026-09-01 STM32 硬件层与样品多角度拍摄合并

- 修改内容：在同一整合分支中同时保留 zdyzzddy 的 STM32 两字节串口硬件层和本地样品多角度旋转拍摄计划；主 UI 可刷新串口、连接/断开 STM32、执行硬件通信自检、滤光轮寻零自检、急停和清故障，同时采集页保留样品台多视角角度计划。
- 修改文件：
  - `host_software/static_ui_prototype_bin/serial_service.py`
  - `host_software/static_ui_prototype_bin/hardware_controller.py`
  - `host_software/static_ui_prototype_bin/device_manager.py`
  - `host_software/static_ui_prototype_bin/rotation_plan.py`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/index.html`
  - `host_software/static_ui_prototype_bin/styles.css`
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`
  - `host_software/static_ui_prototype_bin/tests/test_device_manager.py`
  - `host_software/static_ui_prototype_bin/tests/test_hardware_controller.py`
  - `host_software/static_ui_prototype_bin/tests/test_serial_service.py`
  - `host_software/static_ui_prototype_bin/tests/test_rotation_plan.py`
- 为什么修改：避免硬件整合分支丢失本地多角度拍摄功能，并明确 `sample_rotation` 是样品台多视角计划，`filter_wheel_rotation` 是滤光片转轮波段切换，两者控制域独立。
- 是否影响原有功能：不替换 Model Studio、样品会话、SSC/TA/pH 预测或离线采集；真实相机 SDK 和真实样品台电机仍未接入，`/api/capture/start` 在相机服务接入前仍明确返回不可真实采集。

## 2026-08-30 样品多角度旋转拍摄计划

- 修改内容：主程序样品采集页新增样品多角度旋转拍摄设置，支持启用/关闭、期望角度间隔、起始角度、CW/CCW、闭合补拍；新增 `rotation_plan.py` 统一计算视角数量、实际均分角度、角度序列、闭合 View 和 Home 状态；离线采集在启用多角度时写入兼容命名的 RGB/多光谱多 View 图片、`views.json` 和 metadata 中的 `sample_rotation`。
- 修改文件：
  - `host_software/static_ui_prototype_bin/rotation_plan.py`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/index.html`
  - `host_software/static_ui_prototype_bin/styles.css`
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/tests/test_rotation_plan.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_data_flow.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
- 为什么修改：为后续样品旋转平台硬件接入提前固定采集计划、文件记录和 metadata 结构，同时明确样品旋转角度与滤光片转轮角度是两套完全独立的控制对象。
- 是否影响原有功能：不修改 Model Studio、PLSR/SVR/RF 训练、预测接口或真实硬件通信；默认未启用多角度时保留原离线单视角输出。当前样品台控制仍为模拟状态。

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
