# Changelog

本文档只记录能从 Git 历史或当前代码确认的阶段。无法确认具体日期的内容标记为“历史版本，具体日期待确认”。

## 2026-09-13 P1D-1 Capture-Only Training Sample Workflow

- 修改内容：主程序新建样品新增 `sample_mode=inspection/training_capture`。正常检测继续使用 `/api/quality-models`、Default 和 `generic` fallback 匹配模型；训练数据采集允许自由输入 Fruit Type / Variety，并可在没有任何 Published/Default/Production 模型时创建样品。
- 修改内容：`SessionState` 与样品 `metadata.json` 明确保存 `sample_mode`、`sample_id`、`sample_name`、`fruit_type`、`variety`。Training Capture 下 `selected_ssc_model_id`、`selected_ta_model_id`、`selected_ph_model_id` 保持空字符串，不显示必须先发布模型的提示，也不伪造预测结果。
- 修改内容：Model Studio `import_samples()` 改为优先读取 Sample `metadata.json` 中的 identity；旧样品 metadata 缺少 fruit/variety 时才 fallback 到 Dataset scope。Dataset 为空 scope 且导入样品只有一个 scope 时自动继承；发现多个 fruit_type/variety 时失败为 `DATASET_SAMPLE_SCOPE_CONFLICT` 并返回 scopes 诊断。
- 修改内容：Dataset Version snapshot 增加 Sample identity，Training Experiment scope 自动继承 Dataset scope，Candidate Model metadata 继续记录 `fruit_type`、`variety` 和 target。Production/Default 仍只能人工发布/设置，不由训练自动替换。
- 修改文件：`host_software/static_ui_prototype_bin/index.html`、`styles.css`、`app.js`、`backend_server.py`、`model_studio/service.py`、相关测试与项目文档。
- 是否影响原有功能：不修改 STM32 firmware/protocol、DVP2/RGB adapter、滤光轮、样品台、Dark/White/ROI/Registration 算法或真实硬件 acceptance gate；Inspection 模型匹配和 generic fallback 保留。

## 2026-09-13 P1C-2B Registered Multispectral Conservative ROI

- 修改内容：新增 `quality_algorithm/registered_roi.py`，复用 P1C-2A `RegistrationProfile`、`validate_profile_for_runtime()` 和 `warp_mask_rgb_to_multispectral()`，把 RGB Fruit Mask warp 到 DVP2 坐标后执行 post-warp conservative erosion，输出 ROI pixel counts、bbox、centroid、registration metrics、referenceBandNm、quality flags 和 timing diagnostics。
- 修改内容：`spectral_features.py` 的 `registration_mode="calibrated"` 现在要求显式 `registration_profile`、runtime RGB endpoint 和 runtime DVP2 endpoint；缺 profile、endpoint/profile mismatch、RGB mask resolution mismatch、DVP2 target resolution mismatch、warp empty、erosion empty/too small/retained ratio too low 均失败，不做 identity fallback、resize fallback 或 ellipse fallback。
- 修改内容：同一个 DVP2 registered ROI 在一次 feature extraction 中只构建一次，并复用于所有 enabled spectral bands；每个 band 仍逐张检查 shape，某 band resolution mismatch 会失败。
- 修改内容：保留原有 Dark/White `reflectance_correction()` 与 uncalibrated fallback 数学不变；calibrated mode 下 `roi_pixel_count` 表示最终 eroded DVP2 ROI pixel count。
- 修改内容：新增 `manual_registered_roi_test.py`，可从 RGB binary mask、DVP2 grayscale image 和 registration profile 输出 `rgb_mask.png`、`warped_mask.png`、`eroded_mask.png`、`overlay_warped.png`、`overlay_eroded.png` 与 `diagnostics.json`，仅用于人工 alignment 检查。
- 验收状态：P1C-2B SOFTWARE PASS 目标；planar homography 不是 3D fruit surface 的 pixel-perfect registration，post-warp erosion 只是降低边缘背景/托盘/阴影污染风险的 engineering provisional default。REAL HARDWARE ROI ACCEPTANCE PENDING，REAL BAND-TO-BAND SHIFT ACCEPTANCE PENDING。

## 2026-09-12 P1C-2A.5 Background Reference Fruit Segmentation

- 修改内容：正式 Fruit Mask 方向从 SAM3 改为 Background Reference。原因：当前应用是固定暗箱/相机/光源环境，无训练数据、无 GPU 生产环境、不能引入大模型 checkpoint，也不希望为每种水果维护颜色阈值；背景参考差分更可解释、离线、低维护。
- 修改内容：新增 `quality_algorithm/background_reference.py`，定义 `BackgroundReference`、sha256、save/load、image metadata 和兼容性校验，错误码覆盖 missing image、hash mismatch、resolution/device/camera profile/settings/illumination mismatch 和 invalid reference。参考 age 不单独导致失效。
- 修改内容：新增 `quality_algorithm/background_segmenter.py`，使用 RGB absolute difference + OpenCV Lab distance 加权，执行 threshold -> open -> close -> fill holes -> remove tiny components -> connected components，输出 `FruitSegmentationResult` 诊断字段和质量错误码。
- 修改内容：重写 `quality_algorithm/segmentation.py` 为通用结果结构，保留 `LegacyColorSegmenter` 兼容路径；`mask_quality.py` 保留为通用 IoU/Dice 工具。
- 修改内容：`spectral_features.py` 支持 `segmentation_mode="background_reference"`，要求传入兼容 `BackgroundReference`；背景/样品分辨率不一致时失败，不 resize、不伪造 calibrated registration。默认仍为 `legacy_color` 以保持旧流程兼容。
- 修改内容：新增 `manual_background_segmentation_test.py`，输出 `background.png`、`sample.png`、`difference_map.png`、`raw_mask.png`、`clean_mask.png`、`overlay.png` 和 `diagnostics.json`；新增 `config/background_reference.example.json`，运行时真实参考图和 JSON 仍放在 gitignored runtime/config 路径。
- 修改内容：移除 SAM3 生产入口、worker、checkpoint config、英文 Fruit Type/prompt 要求；Fruit Type 继续只是 Dataset/Model scope，可使用用户输入字符串。
- 验收状态：REAL FRUIT SEGMENTATION ACCEPTANCE PENDING；当前只完成软件算法与 deterministic synthetic tests，不声称覆盖所有水果或达到固定 Mean IoU，真实硬件背景参考采集和人工 mask 验收仍待完成。

## 2026-09-12 P1C-2A RGB-DVP2 Geometric Registration Calibration

- 修改内容：新增 `quality_algorithm/registration.py`，实现 RGB -> DVP2 `planar_homography_v1` 标定 profile、3x3 homography validation、checkerboard corner detection、RANSAC 单应性估计、reprojection error metrics、profile save/load、runtime endpoint/resolution mismatch guard 和 nearest-neighbor mask warp helper。
- 修改内容：新增 `manual_registration_test.py` 离线 CLI，可输入 RGB scientific checkerboard 图与 DVP2 reference-band checkerboard 图，输出 `registration_profile.json`、`diagnostics.json`、`rgb_corners.png`、`ms_corners.png`、`rgb_warped.png`、`overlay.png` 和 `difference.png`。
- 修改内容：新增 `config/registration_profile.example.json` 作为可提交参考；运行时 `config/registration_profile.json` 和 `manual_registration_output/` 被 Git 忽略，不提交本机标定结果或真实设备绑定状态。
- 修改内容：新增 registration 单元测试，覆盖 profile serialize/deserialize、矩阵校验、identity/translation/perspective homography、mask warp、设备/分辨率错用拦截、checkerboard failure、insufficient points、metrics、malformed profile、runtime profile gitignore 和现有 identity ROI path。
- 修改文件：`.gitignore`、`host_software/static_ui_prototype_bin/quality_algorithm/registration.py`、`host_software/static_ui_prototype_bin/manual_registration_test.py`、`host_software/static_ui_prototype_bin/config/registration_profile.example.json`、`host_software/static_ui_prototype_bin/tests/test_registration.py`、项目文档。
- 是否影响原有功能：不修改 UI，不修改 RGB scientific capture、preview restore、YUY2 policy、PNG metadata、DVP2、STM32、钨灯、滤光轮、Dark/White、Model Studio 或 prediction；`quality_algorithm.roi.apply_mask_to_image(registration_mode="calibrated")` 仍未接入生产路径，待 P1C-2B。

## 2026-09-12 Two-Channel Tungsten Web Control

- 修改内容：按 2026-09-11 实机确认映射修正光源语义：PB7/bit0/0x01 为钨灯1 SSR，PB8/bit1/0x02 为钨灯2 SSR，PB9/bit2/0x04 为 LED3，三路统一复用 current firmware `LED_SET=0x12` 和 STATUS `led_mask/led1_duty/led2_duty/led3_duty`，不新增或伪造 0x13 钨灯协议。
- 修改内容：新增集中光源位常量和安全 read-modify-write 控制；LED3 操作不再覆盖 PB7/PB8，钨灯单路操作保留其它位，普通钨灯 all-off 保留 LED3，emergency/safe shutdown 发送全部光源 `led_mask=0x00`。
- 修改内容：新增 `POST /api/device/tungsten` 和 `POST /api/device/tungsten/all-off`，手动开启钨灯限制 1-5 秒，要求风扇 fresh STATUS、无故障、LED3 关闭、无可靠门反馈时操作员人工确认；后端 timer 自动关闭，关闭失败时返回“无法确认钨灯已关闭，请立即切断12V光源电源。”。
- 修改内容：主 UI “光源与滤光轮”新增“钨灯手动测试”卡片，提供钨灯1/2 点亮5秒、立即关闭、全部钨灯关闭、led_mask、led1/led2 duty、STATUS revision、串口和故障码显示；不提供永久开启和双路同时开启。
- 修改内容：True Capture 光源语义修正：Coordinator 默认 RGB 光源不再使用 `0x03`，旧 `rgbLedMask=0x03` 会被 `RGB_LIGHT_MAPPING_NOT_CONFIRMED` 阻断；多光谱默认单路钨灯，`allowDualTungsten=false` 时 `tungstenMask=0x03` 会被 `DUAL_TUNGSTEN_NOT_ACCEPTED` 阻断；Dark 和取消/异常收尾执行全光源关闭验证。
- 修改文件：`host_software/static_ui_prototype_bin/hardware_controller.py`、`stm32_controller.py`、`device_manager.py`、`backend_server.py`、`capture_coordinator.py`、`index.html`、`styles.css`、`app.js`、相关测试与项目文档。
- 是否影响原有功能：不修改 STM32 固件，不新增协议命令，不调用 `manual_stm32_test.py`，不放行未验收双钨灯同时开启，不虚构 RGB 正式照明映射或硬件门端点反馈。

## 2026-09-11 P1C-1 RGB Scientific Profile + YUY2 Capture Approval

- 修改内容：RGB Preview Profile 与 RGB Scientific Capture Profile 明确分离；默认 Preview 保持 `3840x2160 @25fps MJPG`，默认 Scientific 明确为 `1920x1080 @5fps YUY2`；`config/camera_settings.json` 是本机运行时配置并被 Git 忽略，`config/camera_settings.example.json` 作为可提交参考。
- 修改内容：`CameraSettingsStore` 不再在 `scientificProfile` 缺失时静默继承 preview profile；正式 RGB capture 缺少完整 scientific profile 时失败为 `RGB_SCIENTIFIC_PROFILE_NOT_CONFIGURED`。
- 修改内容：`rgb_scientific.py` 新增 `scientificCaptureApproved`、`scientificQualityClass` 和 `chromaSubsampling`。YUY2/YUYV/UYVY 保持 `scientificStrictLossless=false`，但作为 `uncompressed_422` 允许正式科研 PNG；MJPG/JPEG/H264/H265 仍判定为 lossy 并阻断。
- 修改内容：`CameraManager.capture_rgb_frame()` 在正式采集前暂停 preview、释放 preview handle、应用 scientific profile、按 actual mode 判断准入，并在结束后恢复 preview profile；metadata 记录 preview/scientific profile、source compression、chroma subsampling、approved/strict/quality class 和 device/settings 来源。
- 修改内容：`CaptureCoordinator` 与 `DeviceManager.capture_readiness()` 改为按 `scientificCaptureApproved` gate，同时保留 `scientificStrictLossless` 诚实记录；actual FOURCC/尺寸/FPS 与 scientific profile 不一致时阻断为 profile mismatch。
- 修改内容：`manual_camera_test.py --rgb-capture-once` 使用 isolated settings store，把 CLI `--width/--height/--fps/--fourcc` 显式作为 scientific profile，避免 persistent preview MJPG 覆盖本次真实验证目标。
- 修改文件：`host_software/static_ui_prototype_bin/camera_service/config.py`、`settings_store.py`、`rgb_scientific.py`、`rgb_uvc.py`、`manager.py`、`capture_coordinator.py`、`device_manager.py`、`manual_camera_test.py`、`host_software/static_ui_prototype_bin/config/camera_settings.example.json`、相关测试与项目文档。
- 是否影响原有功能：不删除 MJPG preview，不降低 preview 分辨率，不修改 DVP2、STM32、滤光轮、SampleStage、Model Studio 或 RGB-MS registration；YUY2 不被伪装为 strict lossless。

## 2026-09-11 Model Studio Same-Tab Navigation

- 修改内容：主检测页“模型训练”和“打开 Model Studio”入口由新开浏览器页改为当前页跳转 `/model-studio`，避免从模型训练中心返回后多出一个检测中心页面。
- 修改内容：确认 Model Studio 顶部“返回检测工作站”继续使用同页 `href="/"`，不使用 `target="_blank"`。
- 修改文件：`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/tests/test_model_studio_frontend_static.py`、`docs/UI_ARCHITECTURE.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不合并 Model Studio 到主检测页，不修改训练/模型/后端 API，不改变 Operator Workflow 与 Engineer Workflow 分离。

## 2026-09-10 UI-0/UI-1 Main Workstation IA + Instrument Visual Refinement

- 修改内容：主检测软件左侧导航从底层 Task Tree 收敛为五个一级工作区：检测工作台、样品与记录、设备与维护、模型训练、系统设置；Model Studio 继续作为独立 `/model-studio` 工作空间，不合并回主检测页。
- 修改内容：新增 Operator Workbench 总览卡，显示当前样品、STM32/RGB/多光谱/Calibration 就绪状态、样品->设备->采集->分析->结果流程，以及基于现有 state 派生的下一步 Primary Action。该按钮只转发到现有 UI 入口，不绕过 readiness、disabled 或后端 gate。
- 修改内容：调整主界面默认入口为“检测工作台”；设备维护、光源/滤光轮、系统设置等页面默认隐藏全局相机预览，相机检查、相机维护和采集页面保留预览区域。
- 修改内容：新增 `docs/UI_ARCHITECTURE.md` 和 `docs/UI_DESIGN_SYSTEM.md`，固化 Operator/Engineer 分层、Workspace 边界、Surface/Button/Input/Status/Navigation/Modal/Motion 视觉契约；同步 `AGENTS.md` 要求后续主程序 UI 修改先阅读这两份文档。
- 修改内容：在不更换原有 Dark Slate + Purple/Fuchsia/Cyan 配色体系的前提下，为主界面新增兼容 design token：surface、border、shadow、radius、spacing、control height 和 transition；统一 Primary/Secondary/Ghost/Danger/Disabled/Focus 状态，降低卡片阴影和导航 active 饱和度。
- 修改文件：`host_software/static_ui_prototype_bin/index.html`、`host_software/static_ui_prototype_bin/styles.css`、`host_software/static_ui_prototype_bin/app.js`、`docs/UI_ARCHITECTURE.md`、`docs/UI_DESIGN_SYSTEM.md`、`AGENTS.md`、相关项目文档。
- 是否影响原有功能：不修改后端 API、Camera Adapter、DVP2、STM32 protocol、CaptureCoordinator、scientific PNG gate、Model Studio 数据集/训练/发布语义或模型预测逻辑；不把 offline/demo、preview JPEG 或未验收硬件标记为真实能力。

## 2026-09-11 Strict Windows Single Instance

- 修改内容：`launcher.py` 的单实例检查前移到 `main()` 最前段，在 `prepare_runtime_site()`、`start_backend()`、`webbrowser.open()` 和任何 backend/硬件初始化前创建 `Local\FruitTasteAnalyzer.SingleInstance` Named Mutex。
- 修改内容：第二个及以后实例发现 mutex 已存在时只弹出/打印“FruitTasteAnalyzer 已经在运行。”并立即退出；不再读取 runtime port 打开已有 Web UI，不启动第二个 backend，不访问 STM32/RGB/DVP2。
- 修改内容：`process_lock.NamedMutex` 支持 `CreateMutexW(None, False, name)` 语义，首实例保存 handle 到 launcher 生命周期结束，并在 `finally` 中 `CloseHandle`；异常退出尽量清理，强制结束时由 Windows 自动释放 named object，不依赖 lock 文件作为主判断。
- 修改文件：`host_software/static_ui_prototype_bin/launcher.py`、`host_software/static_ui_prototype_bin/process_lock.py`、`host_software/static_ui_prototype_bin/tests/test_launcher_single_instance.py`、项目文档。
- 是否影响原有功能：不修改主 UI、后端 API、相机 adapter、STM32 协议、Model Studio 或真实采集 gate；`FruitTasteAnalyzer.spec` 正式 exe 入口仍是 `launcher.py`。

## 2026-09-10 P1B-8.1 Follow-up: Sample Gate, Model Batch Delete, Single Instance

- 修改内容：`/api/new-sample` 和主 UI “新建样品”不再依赖设备准备状态，只校验样品名称、果种/品种和保存根目录；True Hardware Capture 仍由 `/api/capture/readiness` 和 `/api/capture/start` 严格 gate，并显示所有阻塞原因。existing CalibrationSet 模式要求填写当前样品目录下存在的 `calibrationId`，不再提示可留空。
- 修改内容：Model Studio 永久删除确认框将 Model ID 改为只读 code 文本，确认输入框必须手动输入并按 `trim()` 后精确匹配；Models 页面新增当前列表多选、全选和批量永久删除。新增 `POST /api/model-studio/models/delete-batch`，后端逐个复用单模型永久删除规则，返回 deleted/blocked/failed。
- 修改内容：确认 Default 模型切换已有后端 `set_default_model()` 与 `/api/model-studio/models/default`，本轮补强 UI 入口和刷新：Published/Production 可设为 Default，Candidate/Validated 显示“请先发布模型”，当前 Default 明确标识。后端继续保证同一 fruitType + variety + target 只有一个 Default，旧 Default 自动回到 Published。
- 修改内容：新增 Windows named mutex 单实例保护 `Local\FruitTasteAnalyzer.SingleInstance`，首次 launcher 写 `%LOCALAPPDATA%\FruitTasteAnalyzer\runtime.json`，后续实例不启动第二个 backend、不初始化 STM32、不访问 RGB/DVP2。RGB/DVP2 adapter 另加按 stable identity 的跨进程相机 ownership mutex，并把设备占用状态区分为 `BUSY_BY_OTHER_PROCESS` 而不是“未检测到”。
- 修改内容：`CameraManager.release_all()` 统一停止 RGB/DVP2 preview worker、stop stream、close adapter 并释放跨进程设备锁；launcher、backend shutdown 和异常退出路径尽量调用统一释放。
- 修改文件：`host_software/static_ui_prototype_bin/process_lock.py`、`host_software/static_ui_prototype_bin/launcher.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/camera_service/base.py`、`host_software/static_ui_prototype_bin/camera_service/rgb_uvc.py`、`host_software/static_ui_prototype_bin/camera_service/dvp2_mono.py`、`host_software/static_ui_prototype_bin/camera_service/manager.py`、`host_software/static_ui_prototype_bin/index.html`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/model_studio/service.py`、`host_software/static_ui_prototype_bin/model_studio/static/*`、相关测试与项目文档。
- 是否影响原有功能：不放宽 True Capture readiness，不绕过 Default/Production/reference 删除保护，不强制关闭已有实例相机，不修改 STM32 firmware、DVP2 SDK binding、滤光轮或 SampleStage 真实硬件边界。

## 2026-09-10 P1B-8.1 RGB Strict-Lossless Scientific Capture Gate

- 修改内容：新增 `camera_service/rgb_scientific.py`，集中定义 RGB scientific transport policy。`MJPG/MJPEG/JPEG/H264/H265` 判定为 lossy，`YUY2/YUYV/UYVY` 记录为 `uncompressed_but_chroma_subsampled` 但 strict lossless=false，只有 RAW Bayer/RGB24/BGR24 等已确认完整未压缩 pixel transport 才允许作为 RGB scientific source。
- 修改内容：`CameraManager.capture_rgb_frame()` 不再把 preview MJPG 句柄直接用于正式 scientific capture；若 preview 正在运行，会先暂停/释放 preview handle，再按独立 `scientificProfile` 打开相机、读取 actual FOURCC、验证 `scientificStrictLossless=true` 后才返回帧供 Coordinator 保存 PNG。actual 回读为 MJPG 或 unknown 时分别返回 `RGB_SCIENTIFIC_TRANSPORT_LOSSY` / `RGB_SCIENTIFIC_LOSSLESS_UNAVAILABLE`，不会保存 PNG。
- 修改内容：正式 RGB metadata 增加 `requestedFourcc`、`actualFourcc`、`sourcePixelFormat`、`sourceCompression`、`scientificStrictLossless`、`outputFormat=PNG`、`outputLossless=true`；`CaptureCoordinator` 增加第二层 guard，禁止 lossy source 进入 PNG 保存。
- 修改内容：`DeviceManager.capture_readiness()` 增加 RGB strict-lossless gate；即使 RGB preview 可用，只要 scientific transport 未通过，True Capture readiness 仍 BLOCK。主 UI RGB 相机摘要新增 Preview Transport、Scientific Transport、Scientific Lossless，并在 FAIL 时显示禁止用于科学采集的提示。
- 修改内容：`manual_camera_test.py` 新增 `--rgb-lossless-probe`，可对真实 RGB 相机候选 FOURCC/分辨率/FPS 做一次性 capability probe 并输出简洁表格；若设备一开始无法打开，会停止后续重复候选。当前环境本轮运行时 `device_index=1` DirectShow 打开失败，未找到 strict-lossless mode。
- 修改文件：`host_software/static_ui_prototype_bin/camera_service/rgb_scientific.py`、`host_software/static_ui_prototype_bin/camera_service/config.py`、`host_software/static_ui_prototype_bin/camera_service/settings_store.py`、`host_software/static_ui_prototype_bin/camera_service/rgb_uvc.py`、`host_software/static_ui_prototype_bin/camera_service/manager.py`、`host_software/static_ui_prototype_bin/camera_service/__init__.py`、`host_software/static_ui_prototype_bin/capture_coordinator.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/manual_camera_test.py`、`host_software/static_ui_prototype_bin/index.html`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/styles.css`、相关测试与项目文档。
- 是否影响原有功能：Preview MJPG 仍只用于浏览器预览；正式 scientific RGB capture 不再允许 `MJPG -> decode -> PNG`。不修改 DVP2 Mono8 scientific path、STM32、Filter Wheel、SampleStage、Dark/White、多波段或 Model Studio。

## 2026-09-10 P1B-8.1 Camera Settings Persistence & Restore

- 修改内容：新增 `camera_service/settings_store.py` 和后端权威运行时 `config/camera_settings.json` 配置结构，支持 JSON UTF-8 load/save/reset/migrate legacy，缺文件自动生成 default，损坏 JSON 返回 warning/default，保存使用 atomic replace。字段白名单限制为 RGB device identity/index、width/height/fps/fourcc、auto/manual exposure、gain、white balance 和现有几何缓存，以及 DVP2 device identity、exposure、gain；不开放 DVP2 PixelFormat/trigger/ROI 持久化。
- 修改内容：`CameraManager` 区分 Apply、Apply & Save、Save Default、Restore Saved、Restore Default。`/api/camera/rgb/apply-settings` 和 `/api/camera/multispectral/apply-settings` 支持 `persist=true`，仅在成功下发并回读后写入 store；DVP2 exposure/gain 继续 set 后 get readback；RGB DirectShow 每项参数记录 requested/actual/accepted/supported，不伪造 unsupported 成功。
- 修改内容：新增 `GET /api/camera/settings`、`POST /api/camera/settings/save|reset|restore|migrate-legacy`。restore state 记录 `not_attempted/restored/partial/failed/device_mismatch`、`lastRestoredAt`、`restoreError` 和 per-setting results；恢复触发于 probe、preview/capture open、explicit restore 和 reconnect，而不是每次 status 查询。probe 恢复后仍释放相机句柄，保留 `detected/available` 语义。
- 修改内容：主 UI 相机设置页启动时优先读取后端配置；仅当后端配置不存在且旧 `fruitAnalyzer.cameraSettings` 存在时迁移旧 RGB localStorage。按钮新增/调整为“应用到相机”“应用并保存”“保存为默认配置”“恢复已保存配置”“恢复默认配置”，并显示 saved requested、current actual 和 restore state。
- 修改内容：正式 RGB/DVP2 capture metadata 增加 `settingsSource` 和 `settingsRestoreState`。True Capture readiness 对 saved device identity mismatch 返回 `CAMERA_SETTINGS_DEVICE_MISMATCH` blocker，对 restore failed 返回 warning；无保存配置时 `settingsSource=default` 不构成 blocker。
- 修改文件：`host_software/static_ui_prototype_bin/camera_service/settings_store.py`、`host_software/static_ui_prototype_bin/camera_service/manager.py`、`host_software/static_ui_prototype_bin/camera_service/__init__.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/index.html`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/tests/test_camera_settings_persistence.py`、`host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不修改 STM32 firmware、滤光轮 mapping、SampleStage、preview scientific PNG 边界、DVP2 raw saver、Dark/White、多波段、True Capture 编排或模型训练。相机参数持久化是上位机本地软件配置，不表示写入 RGB/DVP2 EEPROM/UserSet；真实硬件 acceptance 仍需现场验收。

## 2026-09-08 P1B-8 True Capture Integration

- 修改内容：新增 `TrueCapturePlan` 与 `CaptureCoordinator.run_true_capture()`，把已有 Dark/White reference、RGB 正式 PNG、DVP2 raw mono PNG、多波段 sequence 和 Sample MultiView 软件路径编排为 True Hardware Capture 入口。单视角模式强制 `sample_rotation.enabled=false`，不要求真实 SampleStage；多视角模式继续由 `SAMPLE_STAGE_PROTOCOL_UNKNOWN` 阻断。
- 修改内容：`DeviceManager.capture_readiness()` 按当前 capture plan 动态返回 `ready`、`trueCapturePrepared`、`blockingReasons`、`warnings`、`singleView`、`multiView` 和 capabilities。`POST /api/capture/start` 先执行 readiness gate，再调用 `run_true_capture()`；`GET /api/capture/readiness` 与 `/api/status` 暴露同一语义。
- 修改内容：`/api/complete-capture` 明确降级为 Offline/Demo 入口，只能调用 `create_offline_capture_dataset()` 生成离线验证 PNG；显式 true hardware 模式会返回 `TRUE_CAPTURE_USES_CAPTURE_START`，防止把 offline dataset 误标为真实采集。
- 修改内容：主 UI 样品采集页新增 True Hardware Capture 面板，提供单视角/多视角、existing/capture_new/no calibration、Calibration ID、Dark/White 操作员确认、readiness 刷新、开始真实采集和取消采集。多视角在样品台协议未知时禁用并显示 blocking reason。
- 修改内容：新增 `manual_true_capture_test.py`，默认只做 readiness/dry-run；没有 `--allow-hardware` 不会启动真实硬件动作。新增/扩展测试覆盖单视角 capture_new、existing calibration、RGB/DVP2/filter/cancel safe stop、operator confirmation failure、DeviceManager readiness/start 和后端 True Capture API/UI wiring。
- 修改文件：`host_software/static_ui_prototype_bin/capture_coordinator.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/index.html`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/manual_true_capture_test.py`、`host_software/static_ui_prototype_bin/tests/test_capture_coordinator.py`、`host_software/static_ui_prototype_bin/tests/test_device_manager.py`、`host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不修改 STM32 firmware、滤光轮映射、相机驱动、DVP2 SDK binding、preview cache、训练算法或模型生命周期；不把 preview JPEG 写入 scientific capture；不把 offline/demo 数据当真实采集；不把 SampleStage simulation 当 PASS。`trueCapturePrepared` 现在是当前 plan readiness，不等于 hardware acceptance PASS，且 productionAccepted 继续为 false。

## 2026-09-08 P1B-7.5B Sample Stage Boundary + Protocol Unknown

- 修改内容：搜索仓库内 sample stage / rotation stage / 样品台 / 旋转台 / motor / STM32 / 串口 / protocol 等资料后，未找到独立样品旋转台控制器、通信方式、baudrate、命令格式、HOME 传感器、position feedback、busy/fault 或速度设置的真实 contract；现有 `UPPER_COMPUTER_STM32_INTEGRATION_REQUEST.md` 明确写着独立样品旋转台没有真实 STM32 控制接口。结论固定为 `SAMPLE_STAGE_PROTOCOL_UNKNOWN`，不实现猜测性 RealSampleStageAdapter。
- 修改内容：扩展 `sample_stage.py`，新增 `SampleStageStatus`、`SAMPLE_STAGE_PROTOCOL_UNKNOWN`、connect/disconnect/home/move_to/move_relative/stop/get_status/get_position/safe_stop 接口语义。`UnimplementedSampleStage` 默认报告协议未知、无 HOME、无 position feedback；`SimulatedSampleStage` 只用于 unittest/离线软件编排。
- 修改内容：`DeviceManager` 默认持有 `UnimplementedSampleStage` 并暴露 `sample_stage_status()`、`sample_stage_home()`、`sample_stage_move_absolute()`、`sample_stage_move_relative()`、`sample_stage_stop()`，后端新增 `GET /api/device/sample-stage/status` 与 `POST /api/device/sample-stage/home|move-absolute|move-relative|stop`。所有样品台 API 均走 `DeviceManager -> SampleStage adapter`，不从 HTTP handler 直接写串口，不复用滤光轮 STM32 motor。
- 修改内容：主 UI 设备准备页新增样品旋转台调试区，显示连接、当前角度、目标角度、HOME、运动、Fault，并提供 HOME、+30°、-30°、Go 0/30/60/90、STOP、Return Home。默认真实 adapter 因协议未知不可用，按钮禁用/提示，不显示硬件成功。
- 修改内容：`CaptureCoordinator.safe_stop()` 纳入 SampleStage `safe_stop()`，MultiView 失败、取消、超时时会尝试停止样品台，同时保留原有 HardwareController/DeviceManager safe stop 路径。`run_sample_multiview_capture()` 仍复用 P1B-5 RGB/DVP2/滤光轮 sequence，不重新实现采图或波段逻辑。
- 修改内容：新增/扩展测试覆盖 SampleStage unknown/simulation 状态、DeviceManager sample stage API、后端 sample-stage endpoint、UI 调试入口和 MultiView 失败时 stage safe_stop。同步更新 `PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md` 和 `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`；AC-STAGE 继续 `BLOCKED`，并列出 STAGE-01..STAGE-12 未来真实验收项。
- 修改文件：`host_software/static_ui_prototype_bin/sample_stage.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/capture_coordinator.py`、`host_software/static_ui_prototype_bin/index.html`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/tests/test_sample_stage.py`、`host_software/static_ui_prototype_bin/tests/test_device_manager.py`、`host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`、`host_software/static_ui_prototype_bin/tests/test_sample_rotation_capture.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不修改 STM32 firmware、不修改 `CURRENT_FILTER_WHEEL_MAPPING`、不把 filter wheel 和 sample stage 混用、不放行 `/api/capture/start`、不设置 `trueCapturePrepared=true`、不把 simulation test 当作真实样品台 PASS。

## 2026-09-07 P1B Hardware Acceptance Specification

- 修改内容：新增 `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`，作为 P1B 真实硬件与采集系统正式验收规范。文档覆盖 RGB、DVP2、STM32、Fan、LED3、Door/Actuator、Filter Wheel、Dark Reference、White Reference、Dark/White Compatibility、Multi-band Capture、Sample Stage、Multi-view Capture、Scientific Data Integrity、Metadata Integrity、Safety 和 True Capture Gate 等 17 个 acceptance domains。
- 修改内容：验收规范固定状态枚举 `NOT TESTED / IN PROGRESS / PASS / FAIL / BLOCKED / N/A`，并要求记录测试日期、测试人员、application commit、硬件身份、步骤、实测值、截图/日志/PNG/metadata/console output 证据、失败原因和 follow-up。未现场测试的硬件默认 `NOT TESTED`；真实样品台和完整 true capture 当前保持 `BLOCKED`。
- 修改内容：同步 `PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md` 和 `docs/REQUIREMENTS.md`，明确 P1B 已进入 hardware acceptance 阶段，真实采集放行必须经过正式 acceptance gate；unit test、fake adapter 或 simulation 不能把 STM32、风扇、LED、推杆、滤光轮、RGB 浏览器端延迟、DVP2 网页预览、Dark、White、样品旋转台或完整真实采集标记为 PASS。
- 修改文件：`docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：只新增和同步验收文档，不修改业务代码、不放行 `/api/capture/start`、不设置 `trueCapturePrepared=true`、不把任何未现场验证硬件标记为 PASS。

## 2026-09-07 RGB Low-Latency Preview + Scientific PNG Guardrails

- 修改内容：RGB 网页预览改为后台 latest-frame + latest encoded JPEG cache。`CameraManager.start_rgb_preview()` 启动单一 RGB adapter/stream 和后台 worker，持续按当前 RGB 配置取帧、resize、JPEG 编码并覆盖最新缓存；`/api/camera/rgb/preview-frame` 直接返回最近 JPEG cache，不再按每个 HTTP 请求同步调用 `capture_frame()`。probe、preview 和正式 RGB capture 共用同一 capture lock，避免同一 UVC 句柄并发取帧。
- 修改内容：DVP2 多光谱网页预览继续保持 P1B-5.4 latest-frame 架构，并将 resize/JPEG 编码前移到 preview worker，新增 latest encoded JPEG cache；HTTP 预览请求直接读缓存，不再逐请求编码。RGB/DVP2 预览响应和 UI 诊断补齐 `frameId`、`sourceTimestamp/sourceAgeMs`、capture/resize/JPEG/server/browser fetch 耗时、measured FPS、drop 计数和 encoder。
- 修改内容：前端 RGB/DVP2 预览轮询由固定 `setInterval` 改为 fetch 完成后再 `setTimeout` 调度，确保最多一个 in-flight preview 请求；离开相机设置页时会停止 RGB/DVP2 预览和 DVP2 focus 辅助，避免后台页面继续拉流。
- 修改内容：新增 `manual_camera_test.py --rgb-preview-benchmark`，在当前电脑实测 `3840x2160` RGB -> `960x540` 预览编码。OpenCV q80 resize/JPEG 约 0.77ms/6.92ms，PIL resize 约 46ms；old synchronous preview serverTotal 平均约 22.19ms，latest-frame HTTP cache serverTotal 平均约 0.03ms，latest-frame 的 sourceAge 平均约 64.20ms。CLI 不能测浏览器 fetch，主 UI 诊断栏负责现场显示。
- 修改内容：强化正式 scientific capture PNG 边界。受保护 RGB 单帧、DVP2 raw mono 单帧、多波段 sample sequence、Dark/White reference、Sample MultiView 和 `create_offline_capture_dataset()` 离线验证正式数据均由测试覆盖 `.png` 保存，并校验正式 metadata 不引用 `.jpg/.jpeg`。JPEG 仍允许作为浏览器 preview 格式，但不得进入科学采集数据集或 metadata。
- 修改文件：`host_software/static_ui_prototype_bin/camera_service/manager.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/manual_camera_test.py`、`host_software/static_ui_prototype_bin/tests/test_camera_service.py`、`host_software/static_ui_prototype_bin/tests/test_capture_coordinator.py`、`host_software/static_ui_prototype_bin/tests/test_backend_data_flow.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不修改 STM32 firmware，不接入 SampleStage，不修改滤光轮协议，不放行 `/api/capture/start`，`trueCapturePrepared` 继续为 false；不使用 preview JPEG/latest-frame cache 冒充 RGB/DVP2/Dark/White/MultiView 正式采集；DVP2 当前环境枚举仍为 0，需在设备在线且 BasedCam3 退出后做网页端复核。

## 2026-09-06 P1B-7.5A.2 STM32 Web Hardware Control + Fresh Motion Verification

- 修改内容：补齐 STM32 Web 硬件调试入口。设备准备/电机页移除旧的假日志按钮和“滤光轮寻零自检”，新增风扇 on/off、LED3 on/off、推杆伸出/缩回/停止、滤光轮顺/逆时针按 slot 相对移动、滤光轮 STOP、人工确认后“将当前位置设为滤光轮零点”。Fault Clear 在当前 firmware 下禁用并提示 unsupported。
- 修改内容：新增后端设备 API：`POST /api/device/fan`、`/api/device/led`、`/api/device/actuator`、`/api/device/wheel/move-relative`、`/api/device/wheel/stop`、`/api/device/wheel/set-origin`。所有接口走 `DeviceManager -> Stm32ControllerAdapter -> SerialService`，不新建串口服务，不调用 `manual_stm32_test.py`。
- 修改内容：强化 AA55 STATUS 新鲜度和滤光轮运动验收。`Stm32ControllerAdapter` 为 STATUS cache 增加 revision 与 `statusReceivedMonotonic`；`query_status(require_fresh=True)` 不再在 fresh 查询失败时返回旧缓存；滤光轮移动记录命令前状态，Web 手动移动默认 `maxRetries=0`，完成判定要求 post-command fresh STATUS、目标/位置变化、最终位置到达、motor state idle/done 且 errorCode=0。ACK OK 但 target/position 未变化会返回 `motion_not_started` 并发送 STOP，不能盲目重发相对运动。
- 修改内容：推杆控制明确为 DOOR_SET accepted-only 语义，UI 标签使用“推杆伸出/推杆缩回/推杆停止”，后端用 token/generation 定时 STOP，避免旧 timer 停掉新动作。Emergency Stop 继续执行 filter wheel STOP、door STOP、LED off、fan on 的安全策略，并在 UI 文案中说明风扇保持开启。
- 修改内容：通信 self-test 改为非破坏自检，只执行 PING 和 fresh STATUS，不开启风扇、不移动滤光轮；`SET_ORIGIN` 仅表示人工对准 1 号滤光片后建立逻辑零点，不表示自动寻零或 HOME sensor。
- 修改文件：`host_software/static_ui_prototype_bin/stm32_controller.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/index.html`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/styles.css`、`host_software/static_ui_prototype_bin/tests/test_stm32_controller.py`、`host_software/static_ui_prototype_bin/tests/test_device_manager.py`、`host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`、`AGENTS.md`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不修改 STM32 firmware，不接入 SampleStage，不修改相机、DVP2 preview、CaptureCoordinator raw saver、Dark/White、MultiView、Model Studio、训练算法或 `/api/capture/start`；`trueCapturePrepared` 继续为 false。本阶段为软件实现与自动测试通过，真实 STM32 现场验收仍未执行。

## 2026-09-06 Model Studio Dataset / Model Delete Completion

- 修改内容：补齐 Dataset Archive 与 Permanent Delete 闭环。新增 `dataset_references()`、`archive_dataset()`、`delete_dataset_permanently()`，以及 `GET /api/model-studio/datasets/<dataset_id>/references`、`POST /api/model-studio/datasets/archive`、`POST /api/model-studio/datasets/delete`。Dataset Permanent Delete 需要 Dataset Name 确认；Published / Default / Production 模型引用会返回 `DATASET_HAS_PRODUCTION_MODEL_REFERENCES` 并阻止删除；无生产引用时可级联清理 samples、labels、dataset_versions、training_experiments、jobs、Candidate/Validated/Archived models 和受管 artifacts。
- 修改内容：补齐 Model Delete UI 闭环。Model Card 改为“查看 / 重训 / 更多”，更多菜单按 Candidate、Validated、Published、Default、Archived 状态显示合理操作；Delete Permanently 使用红色危险样式和 DOM confirm modal，Default 模型禁用删除并提示先设置另一个兼容默认模型。Published 非 Default 删除会提示删除后不再出现在检测工作站。
- 修改内容：强化 DB / Files 一致性与路径保护。Model/Dataset 删除前先校验待删路径必须落在 `model_studio_data/datasets/`、`model_studio/artifacts/`、`model_studio/models/` 或 `trained_models/published/<model_id>` 等受管边界内；不删除外部 `source_path` / `import_source_path`；仍对应 legacy `trained_models/<target>` default bundle 的模型禁止直接永久删除。
- 修改内容：Dataset List 行和 Dataset Summary 均新增明确的“归档”和“永久删除”入口；删除完成后刷新 Dashboard、Dataset List、Training selection、Model Registry 和主工作台 `/api/quality-models` 可见性。
- 修改文件：`host_software/static_ui_prototype_bin/model_studio/service.py`、`host_software/static_ui_prototype_bin/model_studio/static/index.html`、`host_software/static_ui_prototype_bin/model_studio/static/model_studio.js`、`host_software/static_ui_prototype_bin/model_studio/static/model_studio.css`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/tests/test_model_studio_service.py`、`host_software/static_ui_prototype_bin/tests/test_backend_data_flow.py`、`AGENTS.md`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不修改 STM32、相机、DVP2 preview、RGB preview、CaptureCoordinator、SampleStage、`/api/capture/start`、`trueCapturePrepared`、PLSR/SVR/RF 训练算法或光谱特征数学。

## 2026-09-06 Model Studio Dataset / Training / Model Lifecycle Refinement

- 修改内容：将 Model Studio 一级信息架构整理为 Dashboard / Datasets / Training / Models / Settings。Dashboard 只保留 Dataset、Sample、运行中训练、Published Models 和 Needs Attention；SQLite 路径、滤光片配置和 operation logs 移到 Settings。
- 修改内容：强化 Dataset lifecycle。Working Dataset 可继续 Add Samples、保存标签、Include/Exclude 样品并标记 dirty；Dataset Version 继续冻结 `sample_snapshot_json` / `label_snapshot_json` / `snapshot_hash`，新增 `dataset_version_diff()` 显示 added、removed-or-excluded 和 label changes。重复样品导入支持 Skip、Replace Working Copy、Create New Sample ID、Cancel；Replace 在样品已被历史 Version/Experiment/Model 引用时拒绝。
- 修改内容：强化 Sample 维护和删除保护。Exclude reason 固定为 Image Blur、Missing Band、Calibration Error、Label Error、Damaged Fruit、Outlier、Capture Error、Manual Exclusion、Other；被 Dataset Version、Experiment 或 Model lineage 引用过的 Sample 禁止 Permanent Delete，只能 Exclude。
- 修改内容：修复 Training target 串用风险。前端 Start Training 不再复用旧 `studio.selectedExperimentId`，而是按当前 Dataset Version、target、algorithm、preprocessing、validation 创建新的 Experiment 并启动 Run；后端新增 `create_experiment_and_training_job()` 和 `/api/model-studio/training/start`。
- 修改内容：整理 Experiment / Run / Model Variant 表达。Experiment 是一个 target 的训练配置，Run 是一次 `jobs` 执行，Model Variant 是 Algorithm + Preprocessing 的一个模型结果；训练结果比较页继续按 RMSE 展示，并为低样本、负 R2、未校准、Candidate 显示质量警告。
- 修改内容：重做 Model Registry 展示为 Model Card grid，并提供 search/filter、model detail lineage、Published/Default/Archived 生命周期、Archive 与 Permanent Delete 分离。Permanent Delete 需要 model_id 确认，Default 禁止直接删除，删除时同步清理 SQLite 记录和受管 candidate/published artifacts。
- 修改内容：Retrain 从旧模型带出 `parent_model_id` 创建新的 Experiment / Run / Model，不覆盖旧模型。Published 非 Default 模型继续可被主工作台手动选择；Archived 模型不进入 `/api/quality-models`。
- 修改内容：主工作台加载兼容模型后，在预测前显示真实模型名、version、algorithm、preprocessing 和“已选择，等待预测”，不再误显示“未接入”。手动本地样品目录若缺少 `fruit_type` / `variety` metadata，`/api/sample-folder` 返回 `requiresSampleScope`，前端要求用户选择 scope 后再加载兼容模型。
- 修改文件：`host_software/static_ui_prototype_bin/model_studio/service.py`、`host_software/static_ui_prototype_bin/model_studio/static/index.html`、`host_software/static_ui_prototype_bin/model_studio/static/model_studio.js`、`host_software/static_ui_prototype_bin/model_studio/static/model_studio.css`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/tests/test_model_studio_service.py`、`host_software/static_ui_prototype_bin/tests/test_backend_data_flow.py`、`PROJECT_STATUS.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`、`AGENTS.md`。
- 是否影响原有功能：不修改 STM32、相机、DVP2 preview、RGB preview、CaptureCoordinator、SampleStage、`/api/capture/start` 或 `trueCapturePrepared`。不修改 PLSR/SVR/RF、RAW/SNV/MSC 或光谱特征数学；本轮只处理 Model Studio 管理、UI、lifecycle、lineage 和主工作台模型展示。

## 2026-09-06 P1B-7.5A.1 STM32 Current Firmware Profile Bound

- 修改内容：按当前 STM32 firmware profile 绑定上位机 AA55 production adapter。`stm32_protocol.py` 新增 `CURRENT_STM32_FIRMWARE_PROFILE` 和 `CURRENT_FILTER_WHEEL_MAPPING`，固化 `AA 55 CMD PLEN PAYLOAD CRCH CRCL`、CRC16-CCITT-FALSE 覆盖 `CMD+PLEN+PAYLOAD`、ACK `echo_cmd,result`、STATUS 18 bytes、INFO 13 bytes，以及命令号 `MOVE_ABS=0x01`、`MOVE_REL=0x02`、`STOP=0x03`、`SET_POS_PID=0x04`、`SET_VEL_PID=0x05`、`SET_PROFILE=0x06`、`SET_CONFIG=0x07`、`QUERY_STATUS=0x08`、`SET_ORIGIN=0x09`、`RESET=0x0F`、`FAN_SET=0x10`、`DOOR_SET=0x11`、`LED_SET=0x12`、`RSP_ACK=0x80`、`RSP_STATUS=0x81`、`RSP_INFO=0x82`。STATUS decode 读取 state/err/position_deg/velocity_rpm/target_deg/fan duty/LED1-3 duty；INFO decode 读取 firmwareVersion/ppr/maxRpm/acc。
- 修改内容：`DeviceManager` 默认使用 current profile adapter 包装同一个 `SerialService`，`HardwareController` 保持语义 API；FAN_SET 发送 duty 0/100，LED_SET 支持 bitmask 0x00..0x07，DOOR_SET 只记录 command accepted，不伪造成门到位。滤光轮 mapping 为 16 slots、1600 ppr、22.5°/slot、默认 10 rpm，MOVE_ABS/MOVE_REL payload 为 float32 LE degrees + rpm，主机不发送 pulses。PPR mismatch 只报告并要求人工确认，不自动 SET_CONFIG。
- 修改内容：`stm32_controller.py` 增加 handshake 诊断、INFO cache、`currentFirmwareProfileValidated`、PPR consistency diagnostic、SET_ORIGIN 语义说明和集中 position tolerance；`manual_stm32_test.py` 默认无 motion，可 listen/query STATUS/INFO，只有 `--allow-motion --validate-wheel-22p5` 才执行人工确认后的 +22.5°/-22.5° 低速运动验证。
- 修改文件：`host_software/static_ui_prototype_bin/stm32_protocol.py`、`host_software/static_ui_prototype_bin/stm32_controller.py`、`host_software/static_ui_prototype_bin/hardware_controller.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/manual_stm32_test.py`、`host_software/static_ui_prototype_bin/tests/test_stm32_protocol.py`、`host_software/static_ui_prototype_bin/tests/test_stm32_controller.py`、`host_software/static_ui_prototype_bin/tests/test_hardware_controller.py`、`host_software/static_ui_prototype_bin/tests/test_device_manager.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`、`AGENTS.md`。
- 是否影响原有功能：不修改 STM32 firmware，不改滤光轮协议以外的业务流程，不接入 SampleStage，不修改 CaptureCoordinator step plan、DVP2 raw capture/save、preview cache、Dark/White、MultiView 或 UI；非零 tungsten 仍明确 unsupported；SET_ORIGIN 不等于 automatic HOME sensor；STATUS position 仍是 logical position，不标记 physical encoder verified；`/api/capture/start` 仍返回 409，`trueCapturePrepared` 继续为 false。真实 STM32 smoke 和机械 +22.5°/-22.5° 验收仍需现场执行。

## 2026-09-06 P1B-7.5A STM32 Current Firmware Host Integration Boundary

- 修改内容：新增 `stm32_protocol.py`，实现 AA55 frame encode/decode、CRC16-CCITT-FALSE、stream parser（partial frame、multiple frames、noise bytes、CRC error resync）、`Stm32ProtocolProfile`、STATUS decode 边界和 `FilterWheelMapping`。新增 `stm32_controller.py`，在单一 `SerialService` owner 上实现 current-firmware adapter：ACK 必须按 echo cmd 匹配，STATUS/INFO 可交错进入最新状态缓存，控制命令用 command serialization lock 串行化，MOVE_REL 超时后做 status-aware retry，避免 ACK 丢失但电机已 moving/done 时重复发送相对运动。`SerialService` 增加 raw `write_bytes()` / `read_bytes()` / `clear_buffers()`，旧两字节 `send_command()` 保持兼容；`DeviceManager` 支持注入 `Stm32ProtocolProfile` 后把同一个串口连接包成 `Stm32ControllerAdapter` 再交给 `HardwareController`。新增 `manual_stm32_test.py`，默认不运动，运动必须 `--allow-motion`。`HardwareController` 新增 `CapabilityUnavailableError`，current-firmware adapter 声明不支持 tungsten 时非零钨灯请求明确失败。本条是 adapter 边界阶段，P1B-7.5A.1 已按用户提供的当前 firmware profile 补齐默认绑定。
- 是否影响原有功能：不修改 STM32 firmware，不 merge STM32/main，不 commit/push；不把 STM32 motor 接到 `SampleStage`；不修改 CaptureCoordinator band loop、DVP2 raw saver、preview cache、Dark/White raw capture 或 MultiView 语义；不实现 tungsten fake success，不把 door ACK 当 endpoint verified，不把 SET_ORIGIN 当 automatic HOME sensor，不把 STATUS position 当 CL57C physical encoder feedback；`/api/capture/start` 仍返回 409，`trueCapturePrepared` 继续为 false。

## 2026-09-06 P1B-7 Sample Rotation Multi-View Acquisition Orchestration

- 修改内容：扩展 `CaptureCoordinator`，新增 `SampleViewPlan`、`SampleMultiViewPlan` 和 `run_sample_multiview_capture()`，并新增 `sample_stage.py` 中的 `UnimplementedSampleStage` / `SimulatedSampleStage` 高层样品台边界。MultiView 计划继续复用 `rotation_plan.build_capture_rotation_plan()` 的 `expectedIntervalDeg`、`startAngleDeg`、`direction`、`includeClosureView` 和默认不重复 360° 规则。每个 View 固定执行样品台移动、位置确认、样品台 settling、RGB raw PNG 保存、复用 P1B-5 的 DVP2 多波段 sequence，再做 view completeness；最终写 `metadata.json` 和 `views.json`，记录 `completedViews`、`failedView`、`pendingViews`、`partialCapture`、`multiViewCaptureComplete`、`returnedHome` 和 `homeStatus`。后端新增受保护开发接口 `POST /api/capture/sample-multiview`，不改变完整主采集入口。
- 修改文件：`host_software/static_ui_prototype_bin/sample_stage.py`、`host_software/static_ui_prototype_bin/capture_coordinator.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/tests/test_sample_rotation_capture.py`、`host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`、`AGENTS.md`。
- 是否影响原有功能：不修改 STM32 firmware、串口协议或 raw serial command；硬件模式没有真实样品台 adapter 时明确返回 `hardware_not_implemented`，不会自动 fallback 到 simulation 并标记成功。Dark/White `CalibrationSet` 只被 sample metadata 引用，不会每 View 重拍；RGB 和 DVP2 正式 raw 保存路径保持原位深边界；P1B-5.5 focus quality 未继续扩展；`/api/capture/start` 仍返回 409，`trueCapturePrepared` 继续为 false。状态为 SOFTWARE IMPLEMENTED，真实样品台 STM32 能力和现场联调留给 P1B-7.5。

## 2026-09-06 P1B-6 Dark / White Multispectral Calibration Capture

- 修改内容：扩展 `CaptureCoordinator`，新增 `CaptureReferenceType`、`CalibrationSet`、`run_dark_reference_capture()`、`run_white_reference_capture()` 和 `validate_calibration_compatibility()`。Dark/White 复用 P1B-5 的 `MultispectralCapturePlan` / `MultispectralBandPlan`、滤光轮 HOME/相对移动/位置确认/settling、每 band exposure/gain 设置和 P1B-4 raw mono PNG saver；默认保存 `calibration/dark/band_XX_<bandId>.png` 与 `calibration/white/band_XX_<bandId>.png`，写入 `calibration/calibration_set_<calibrationId>.json`。metadata 明确 `captureType=sample|dark|white`，记录 requested/actual exposure/gain、min/max/mean/std、Dark leakage、White saturation/uniformity、completed/missing bands、`calibrationComplete`、`partialCapture` 和 `sameBandSettingsMatched`。Dark 先关闭采集光源并用现有 output status 验证 lights off；White 要求 operator confirmation 后准备多光谱照明。后端新增受保护开发接口 `POST /api/capture/calibration/dark` 和 `/api/capture/calibration/white`，不改变完整采集入口。
- 修改文件：`host_software/static_ui_prototype_bin/capture_coordinator.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/tests/test_capture_coordinator.py`、`host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`、`AGENTS.md`。
- 是否影响原有功能：不修改 STM32 firmware 或串口协议，不计算 reflectance，不实现样品旋转，不开放 `/api/capture/start`，`trueCapturePrepared` 继续为 false；不使用 preview JPEG/latest-frame cache 做 calibration，不归一化、不把 `uint16` 转 `uint8`，不继续扩展 P1B-5.5 focus quality。状态为 SOFTWARE IMPLEMENTED，暗白物理校正和真实滤光轮联动仍需现场验收。

## 2026-09-06 P1B-5.4 DVP2 Low-Latency Preview

- 修改内容：为多光谱网页预览增加 low-latency latest-frame 路径。`CameraManager.start_multispectral_preview()` 启动单一 DVP2 adapter/stream 后创建后台采集线程，持续读取最新 raw mono frame 并覆盖 cache；`/api/camera/multispectral/preview-frame` 只读取最新 frame 做预览编码，允许丢弃旧帧，不按 HTTP 请求排队调用 DVP2 `get_frame()`。响应头和 UI 信息栏新增 `frameId`、`sourceTimestamp`、`captureDurationMs`、`resizeDurationMs`、`jpegEncodeDurationMs`、`serverTotalMs`、`browserFetchDurationMs`、`measuredPreviewFps`、drop 计数和 encoder。经本机 2048x1200 Mono8 -> 960x540 微基准，PIL resize/JPEG 约 6.5ms/20.2ms，OpenCV resize/JPEG 约 1.0ms/7.7ms，因此多光谱预览优先使用 OpenCV resize/imencode，PIL 保留为 fallback。默认多光谱预览目标从 8 FPS 提升到 12 FPS，前端轮询保持单个 in-flight 请求。
- 修改文件：`host_software/static_ui_prototype_bin/camera_service/manager.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/index.html`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/tests/test_camera_service.py`、`host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`、`AGENTS.md`。
- 是否影响原有功能：不修改 STM32，不修改滤光轮协议，不改变 P1B-4/P1B-5 scientific raw capture/save，不用 preview JPEG 或 latest-frame cache 冒充正式采集，不改变 `uint8/uint16` raw PNG 保存和滤光轮同步 metadata；`/api/capture/start` 仍受保护，`trueCapturePrepared` 继续为 false。

## 2026-09-06 P1B-5 滤光轮 + DVP2 多波段同步采集软件路径

- 修改内容：扩展 `CaptureCoordinator`，新增 `MultispectralBandPlan` / `MultispectralCapturePlan` 和 `run_multispectral_sequence()`。该路径复用 P1B-2 多光谱安全准备链，在关灯收尾前按 filter config 或显式 band plan 对 enabled bands 执行滤光轮 HOME、相对移动、位置确认、稳定等待、按波段曝光/增益应用和 DVP2 raw mono PNG 保存。每个 band 复用 P1B-4 的 raw mono 保存边界，保留 `uint8/uint16` 单通道位深，默认写入 `<multispectralDirName>/band_XX_<bandId>.png`。metadata 新增 `bands`、`multispectralSequence`、completed/pending/failed bands、filter config source/version、developmentConfig、partial/cancelled 状态，并在每帧记录 `bandId`、`wavelengthNm`、`bandAssignment` 和 `filterWheelSynchronized=true`。失败、取消或超时会执行 `safe_stop()`，已保存的前序 band 保留并标记 partial，后续 band 不继续采集。`manual_camera_test.py` 新增 `--multispectral-sequence` 实机验收入口，要求显式 `--confirm-wheel-motion` 和 STM32 端口。
- 修改文件：`host_software/static_ui_prototype_bin/capture_coordinator.py`、`host_software/static_ui_prototype_bin/manual_camera_test.py`、`host_software/static_ui_prototype_bin/tests/test_capture_coordinator.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`、`AGENTS.md`。
- 是否影响原有功能：不修改 STM32 固件，不直接调用 `SerialService.send_command()`，不实现绝对 GOTO，不实现暗场/白板采集，不驱动样品旋转，不修改 RGB capture/save，不开放 `/api/capture/start`，`trueCapturePrepared` 继续保持 false。真实滤光轮硬件联调仍需现场验收。

## 2026-09-05 P1B-4 DVP2 多光谱正式 raw 单帧采集与保存

- 修改内容：新增 `CameraManager.capture_multispectral_frame()`，通过当前绑定/持有的 `Dvp2MonoCamera` 获取正式 DVP2 `MONO` raw `CameraFrame` 和 requested/actual/device 状态快照；预览运行中复用当前 stream，预览停止时临时 open/start/capture/stop/close，异常路径也会释放临时 stream。扩展 `CaptureCoordinator.run_multispectral_capture()`，复用 P1B-2 多光谱安全准备链，在 `capture_safety_check` 后执行 `multispectral_capture`，只接受非空二维单通道 `uint8`/`uint16` 帧，默认保存 `<multispectralDirName>/multispectral_frame_000.png`。写入前拒绝覆盖已有文件，使用临时 PNG 写入，读回验证尺寸、单通道和 dtype 后替换最终文件并再次验证。metadata `frames` 记录路径、宽高、dtype、PixelFormat、曝光/增益、frame stats、设备信息、requested/actual settings 和预览复用状态，并固定 `wavelengthNm=null`、`bandAssignment=unassigned`、`filterWheelSynchronized=false`；手动相机测试脚本新增 `--multispectral-capture-once` 入口用于实机验收。
- 修改文件：`host_software/static_ui_prototype_bin/camera_service/manager.py`、`host_software/static_ui_prototype_bin/camera_service/dvp2_mono.py`、`host_software/static_ui_prototype_bin/capture_coordinator.py`、`host_software/static_ui_prototype_bin/manual_camera_test.py`、`host_software/static_ui_prototype_bin/tests/test_camera_service.py`、`host_software/static_ui_prototype_bin/tests/test_capture_coordinator.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`、`AGENTS.md`。
- 是否影响原有功能：不修改 RGB capture/save 行为，不驱动滤光轮，不实现多波段循环，不采集暗场/白板，不驱动样品旋转，不修改 UI 正式采集入口，不开放 `/api/capture/start`，`trueCapturePrepared` 继续保持 false。

## 2026-09-05 P1B-3.7 相机接口识别、设备选择与独立设备检测收尾

- 修改内容：修复主 UI 相机设置页 `setCameraSettingsTab()` 调用未定义 `$$` 的前端错误；新增 `/api/device/check` 独立设备检查入口，让 STM32、RGB、DVP2 分域检查，STM32 未连接或缺少 pyserial 时不阻断 RGB/DVP2；`requirements.txt` pin 到 `pyserial==3.5`。增强设备发现和绑定语义：按角色校验 kind，Windows RGB discovery 尝试读取 FriendlyName、PnP InstanceId、VID/PID、USB serial 等信息，只有 exact/verified 映射才生成 RGB stableId，order/index 推断只标 `mappingConfidence=inferred` 和 `potentialStableId`；DVP2 discovery 继续只走 DVP2 SDK，并在能可靠匹配时显示主机 IPv4 网卡。相机设置页新增 RGB/DVP2 候选选择与绑定状态，绑定同步 Device Preparation 与 Camera Settings，并更新运行时 RGB `deviceIndex` / DVP2 stable identity。
- 修改文件：`host_software/static_ui_prototype_bin/device_discovery.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/index.html`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/requirements.txt`、`host_software/static_ui_prototype_bin/tests/test_device_discovery.py`、`host_software/static_ui_prototype_bin/tests/test_device_manager.py`、`host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`、`PROJECT_STATUS.md`、`docs/PROJECT_CONTEXT.md`、`docs/REQUIREMENTS.md`、`docs/ARCHITECTURE.md`、`docs/CHANGELOG.md`、`AGENTS.md`。
- 是否影响原有功能：不修改 STM32 固件协议，不合并 STM32 分支，不实现 P1B-4/DVP2 正式采集，不开放 `/api/capture/start`，不改变 CaptureCoordinator RGB 单帧保存业务行为，`trueCapturePrepared` 继续保持 false。

## 2026-09-05 P1B-3.6 统一设备发现、识别与选择基础层

- 修改内容：新增 `device_discovery.py`，定义 JSON-friendly 的 `DeviceCandidate`、`DeviceBinding`、`DeviceRegistry`、`DeviceDiscovery` 和逻辑角色 `MAIN_CONTROLLER`、`ROTATION_CONTROLLER`、`RGB_CAMERA`、`MULTISPECTRAL_CAMERA`。串口 discovery 枚举 pyserial 可提供的 `VID/PID/serial_number/manufacturer/product/location/hwid`，并对未占用 COM 只执行 open/PING/close；已连接端口标记 `inUse=true`，不二次抢占。RGB discovery 扫描有限 OpenCV DirectShow index，记录 `deviceIndex/backend/opened/frameReadable/width/height/fps/fourcc`，但 `stableId=null`，不把 index 当永久身份，也不自动选择第一台。DVP2 discovery 复用 SDK 枚举信息，按 serial/original serial/user id/friendly name 建立候选，稳定身份优先 serial。新增运行时 `runtime/hardware_profile.json` 绑定文件，保存 stableId 与 `lastPort/lastDeviceIndex/backend` 这类 last known location cache。后端新增 `/api/devices/discover`、`/api/devices/bindings`、`/api/devices/bind`；主 UI 设备准备页新增最小设备选择面板，支持扫描候选、按角色选择并保存绑定。
- 修改文件：`host_software/static_ui_prototype_bin/device_discovery.py`、`host_software/static_ui_prototype_bin/serial_service.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/backend_server.py`、`host_software/static_ui_prototype_bin/index.html`、`host_software/static_ui_prototype_bin/styles.css`、`host_software/static_ui_prototype_bin/app.js`、`host_software/static_ui_prototype_bin/tests/test_device_discovery.py`、`host_software/static_ui_prototype_bin/tests/test_serial_service.py`、`host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`、`docs/PROJECT_CONTEXT.md`、`docs/REQUIREMENTS.md`、`docs/ARCHITECTURE.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不修改 STM32 固件协议，不新增 GET_DEVICE_TYPE 假回复，不执行门/电机/光源/滤光轮动作，不实现 DVP2 正式 capture，不强行通过 RGB 实机验收，不自动选择第一台相机，不开放 `/api/capture/start`，`trueCapturePrepared` 继续保持 false。

## 2026-09-05 P1B-3 CaptureCoordinator RGB 正式单帧采集与保存

- 修改内容：新增 `CameraManager.capture_rgb_frame()`，通过同一个 `RgbUvcCamera` adapter 获取正式 RGB `CameraFrame` 和 requested/actual/device 状态快照；预览运行中复用已有 UVC 句柄，预览停止时临时打开、取帧后关闭。扩展 `CaptureCoordinator.run_rgb_capture()`，复用 P1B-2 RGB 安全准备链，在 `capture_safety_check` 后执行 `rgb_capture`，校验 RGB `uint8` H×W×3 非空帧，默认保存 `<rgbDirName>/rgb_view_000.png`，写入前拒绝覆盖已有文件，使用临时 PNG 写入并替换最终文件，确认最终文件存在且大小大于 0 后才记录 metadata。metadata `frames` 记录路径、宽高、通道、dtype、RGB/source pixel order、设备信息、requested/actual settings 和预览复用状态；失败、取消和超时继续进入 `safe_stop()`。
- 修改文件：`host_software/static_ui_prototype_bin/camera_service/manager.py`、`host_software/static_ui_prototype_bin/capture_coordinator.py`、`host_software/static_ui_prototype_bin/tests/test_camera_service.py`、`host_software/static_ui_prototype_bin/tests/test_capture_coordinator.py`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不实现 DVP2 正式多波段采集，不驱动滤光轮或样品旋转，不修改 RGB preview JPEG/UI，不修改 `create_offline_capture_dataset()`，不放行 `/api/capture/start`，`trueCapturePrepared` 继续保持 false。

## 2026-09-05 P1B-2 CaptureCoordinator 真实安全准备链

- 修改内容：扩展 `capture_coordinator.py`，新增 `run_preparation(mode="rgb"|"multispectral")` 和硬件准备步骤：`hardware_precheck`、`door_close`、`fan_on`、`rgb_light_prepare`/`multispectral_light_prepare`、`capture_safety_check`、`lighting_shutdown`。准备链只通过 `HardwareController` 高层 API 调用 PING、故障码、升降门、风扇、RGB LED、钨灯和 `ensure_*_capture_ready()` interlock，不直接发送串口命令；每步可记录 `result` 到 snapshot/metadata。失败、取消和超时继续执行 best-effort `safe_stop()`。
- 修改文件：`host_software/static_ui_prototype_bin/capture_coordinator.py`、`host_software/static_ui_prototype_bin/tests/test_capture_coordinator.py`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/REQUIREMENTS.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不实现 RGB/DVP2 正式采图保存，不驱动滤光轮多波段循环，不驱动样品旋转，不写正式 metadata，不放行 `/api/capture/start`，`trueCapturePrepared` 继续保持 false，`create_offline_capture_dataset()` 仍作为当前离线验证路径保留。

## 2026-09-05 P1B-1 CaptureCoordinator 架构骨架

- 修改内容：新增 `capture_coordinator.py`，定义 `CaptureState`、`CaptureStepStatus`、`CaptureStep`、`CaptureStepPlan`、`CaptureRun`、`CaptureCoordinatorError`、`CaptureCancelled`、`CaptureStepTimeout`、`CaptureSafetyError` 和 `CaptureCoordinator`；支持同步 dry-run 步骤执行、JSON-friendly snapshot、取消请求、步骤超时 metadata、失败/取消 best-effort safe stop 和 `capture_metadata_skeleton.json` 骨架写入。`DeviceManager` 初始化并暴露 coordinator snapshot，但 `start_capture()` 仍抛 `CameraIntegrationRequired`。
- 修改文件：`host_software/static_ui_prototype_bin/capture_coordinator.py`、`host_software/static_ui_prototype_bin/device_manager.py`、`host_software/static_ui_prototype_bin/tests/test_capture_coordinator.py`、`host_software/static_ui_prototype_bin/tests/test_device_manager.py`、`docs/PROJECT_CONTEXT.md`、`docs/ARCHITECTURE.md`、`docs/CHANGELOG.md`。
- 是否影响原有功能：不实现真实 RGB/DVP2/光源/滤光轮/样品旋转采集，不写真实帧，不放行 `/api/capture/start`，`trueCapturePrepared` 继续保持 false，`create_offline_capture_dataset()` 仍作为当前离线验证路径保留。

## 2026-09-04 P1A-2B DVP2 多光谱网页预览与参数控制

- 修改内容：在既有 DVP2 `ctypes` binding 和 `Dvp2MonoCamera` 基础上完成多光谱黑白相机网页预览链路；新增 `/api/camera/multispectral/apply-settings`，支持曝光时间和增益通过 UI -> Backend -> `CameraManager` -> `Dvp2MonoCamera` -> DVP2 SDK 下发并回读实际值；`/api/camera/multispectral/preview/start` 保持同一个 DVP2 实例 open/streaming，`preview-frame` 只读取当前流并输出 960x540 JPEG；预览转换只用于浏览器显示，底层 `CameraFrame` 保留原始 `uint8/uint16` dtype；状态字段拆分 `detected/available/opened/streaming`，`connected` 仅作为 detected 兼容别名；修正相机 IP 严格解析、MAC 显示、`streamFps` 与 `linkSpeedMbps` 分离、当前 `pixelFormat/frameDtype` 与 `supportedPixelFormats` 分离；已发现但无法打开时提示关闭 BasedCam3 或其他相机程序。
- 实机验证：用户已确认在完全退出 BasedCam3 后，`manual_camera_test.py --multispectral --sdk-dir "D:\Netease\DVP2 SDK CN" --serial GP23400004963 --frames 30 --save` 能完成打开、参数读取、开始取流、30 帧取图和 PNG 保存，实际帧为 `2048x1200`、`Mono8`、`uint8`、曝光 `10000.0 us`、增益 `1.0`。本轮 Codex 复测时当前运行环境 `dvpRefresh/dvpEnum` 返回 0，因此网页实时预览的现场可视化在本轮未再次验证通过；代码不能据此伪造成功状态。
- 修改文件：
  - `host_software/static_ui_prototype_bin/index.html`
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/camera_service/dvp2_mono.py`
  - `host_software/static_ui_prototype_bin/camera_service/manager.py`
  - `host_software/static_ui_prototype_bin/manual_camera_test.py`
  - `host_software/static_ui_prototype_bin/tests/test_camera_service.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `AGENTS.md`
- 为什么修改：把多光谱黑白相机从“可枚举/可手动验证”推进到普通用户能在相机设置页完成重新检测、打开预览、调整曝光/增益、查看真实设备信息和错误提示的闭环，同时继续保护原始科学帧数据。
- 是否影响原有功能：不修改 RGB 正常链路、STM32/滤光轮/光源/样品台逻辑、`CameraIntegrationRequired`、`trueCapturePrepared`、`create_offline_capture_dataset()`、图像目录工作流或模型逻辑；不开放 PixelFormat 切换，不进入 CaptureCoordinator，不推送或合并代码。

## 2026-09-03 P1A-2 DVP2 多光谱相机真实枚举层

- 修改内容：新增 `camera_service/dvp2_binding.py`，按真实 `DVPCamera.h` 和官方示例绑定 `dvpRefresh`、`dvpEnum`、`dvpOpenByName`、`dvpOpenByUserId`、`dvpStart`、`dvpGetFrame`、ROI、曝光、增益、触发和帧计数等 DVP2 C API；`Dvp2MonoCamera` 改为真实 SDK 发现 + 枚举 + serial/user_id 选择目标设备 + mono `uint8/uint16` 帧转换边界；`CameraManager` 增加多光谱 probe 和 JPEG 预览链路；后端新增 `/api/camera/multispectral/probe`、`/api/camera/multispectral/preview/start`、`/api/camera/multispectral/preview-frame`、`/api/camera/multispectral/preview/stop`；相机设置页的多光谱面板增加重新检测、预览和只读 DVP2 状态显示；手动相机测试脚本增加 `--multispectral`。
- 实机验证：Python 3.12 `ctypes` 可加载 `D:\Netease\DVP2 SDK CN\library\Visual C++\bin\x64\DVPCamera64.dll`，`dvpRefresh/dvpEnum` 真实枚举到 1 台 `MGV231M-H2`，IP/link `169.254.25.110`，`UserID=GP23400004963`，SDK serial `DSGP23400004963`；`dvpOpenByName` 和 `dvpOpenByUserId` 当前超过 8s 不返回，`dvpOpen(index=0)` 返回 `DVP_STATUS_NO_DEVICE_FOUND`，所以参数查询、开始取流、首帧、30 帧稳定性和正式保存仍未完成。
- 修改文件：
  - `host_software/static_ui_prototype_bin/index.html`
  - `host_software/static_ui_prototype_bin/app.js`
  - `host_software/static_ui_prototype_bin/backend_server.py`
  - `host_software/static_ui_prototype_bin/camera_service/dvp2_binding.py`
  - `host_software/static_ui_prototype_bin/camera_service/dvp2_mono.py`
  - `host_software/static_ui_prototype_bin/camera_service/manager.py`
  - `host_software/static_ui_prototype_bin/manual_camera_test.py`
  - `host_software/static_ui_prototype_bin/tests/test_camera_service.py`
  - `host_software/static_ui_prototype_bin/tests/test_backend_device_api.py`
  - `docs/PROJECT_CONTEXT.md`
  - `docs/REQUIREMENTS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
- 为什么修改：把 DVP2 从“只能发现 DLL 的骨架”推进到基于真实 SDK/header 的 Python 绑定和枚举层，同时用 probe 子进程超时保护避免厂商 DLL open 阻塞主后端。
- 是否影响原有功能：不修改 RGB 正常链路、STM32/滤光轮/光源/样品台逻辑、`CameraIntegrationRequired`、`trueCapturePrepared`、`create_offline_capture_dataset()`、图像目录工作流或模型逻辑；不提交厂商 SDK 二进制。

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
