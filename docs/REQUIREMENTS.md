# Requirements

更新时间：2026-09-06

本文档集中记录当前已经确认或待确认的需求。状态含义：

- 已实现：当前代码中有真实功能。
- 部分实现：有 UI/API/算法骨架，但缺少真实硬件、真实数据、生产模型或完整闭环。
- 未实现：当前代码没有对应实现。
- 模拟：当前仅用于离线演示/调试，不代表真实功能。

## 已确认需求

| 需求 | 状态 | 说明 |
| --- | --- | --- |
| 软件应是水果/果实口感多光谱无损检测系统 | 部分实现 | 主界面、样品流程、形态、模型入口、STM32 硬件控制层和 RGB UVC adapter 已存在；完整真实采集闭环未接入 |
| 使用 RGB 彩色相机采集外观图像 | 部分实现/实机验证 | UI 和目录支持默认 `rgb/`，并支持用户自定义 RGB 子目录名；`RgbUvcCamera` 已在当前电脑通过 OpenCV DirectShow/UVC 验证 `device_index=1`、`MJPG`、`3840x2160`、`25fps`，可返回 RGB `uint8` 帧；相机设置页已支持 `/api/camera/rgb/probe` 真实重新检测、真实参数应用和 960x540 JPEG 预览；P1B-3 已接入 `CameraManager.capture_rgb_frame()` 和 Coordinator 受保护单帧 RGB PNG 正式保存，但完整真实采集入口仍未放行 |
| 使用黑白相机 + 滤光片转轮采集多光谱图像 | 部分实现/实机验证 | 目录和算法支持默认 `multispectral/`，并支持用户自定义多光谱子目录名；滤光轮已有 STM32 HOME/相对旋转控制层；目标黑白相机是 DO3THINK/度申 GigE/RJ45 工业相机，`Dvp2MonoCamera` 已基于真实 DVP2 header/examples 完成 Python 3.12 `ctypes` 绑定；用户已确认完全退出 BasedCam3 后 manual test 可打开、参数读取、取流、30 帧取图和 PNG 保存；网页相机设置页已接入 DVP2 重新检测、实时预览、曝光/增益应用和回读；P1B-4 已接入受保护 DVP2 raw mono 单帧正式 PNG 保存；P1B-5 已接入受保护滤光轮 + DVP2 多波段 sequence 软件路径，可按 filter config/显式 band plan 保存 enabled bands raw PNG；P1B-6 已接入受保护 Dark/White 多波段 reference sequence 软件路径；样品旋转和完整 `/api/capture/start` 仍未完成 |
| 使用封闭暗箱和稳定光源 | 部分实现 | 升降门、RGB LED 两路、钨灯两路、风扇已有 STM32 控制命令；P1B-2 已在 CaptureCoordinator 中接入安全准备链，可按 RGB/多光谱模式准备互斥光源并确认 interlock；P1B-3/P1B-4/P1B-5/P1B-6 的受保护 RGB/DVP2 单帧、多波段 sample 和 Dark/White reference 保存会复用对应安全准备和关灯收尾；Dark reference 会先关采集光源并通过 output status 验证关闭；亮度闭环和完整真实采图同步未实现 |
| 支持暗场和白板校正 | 部分实现/SOFTWARE IMPLEMENTED | 算法有反射率校正；P1B-6 已在 CaptureCoordinator 中新增 `run_dark_reference_capture()` 和 `run_white_reference_capture()`，复用 `MultispectralCapturePlan`/`MultispectralBandPlan`、滤光轮同步、每 band exposure/gain 设置和 raw `uint8/uint16` PNG saver，写入 `CalibrationSet`、`calibrationId`、`captureType`、完整性和诊断 metadata；物理遮光/标准白板放置和真实校准质量仍需现场验收 |
| 样品采集必须先完成设备准备流程 | 已实现/部分真实 | 前端和后端要求先完成设备检查；当前离线验证门槛为控制器/电机/光源控制，P1B-2 coordinator 可执行真实 STM32 安全准备链；即使 P1B-6 内部多波段 sequence 和 Dark/White calibration sequence 可用，`trueCapturePrepared` 在真实滤光轮验收、样品旋转同步、物理校正验收和完整 `/api/capture/start` 闭环完成前仍保持 false，真实完整采集不可用 |
| 主界面应显示统一全局系统状态 | 已实现 | 顶栏新增“当前状态”，由 `deriveSystemStatus()` 基于设备、样品、离线验证、形态任务和预测状态派生 |
| 设备准备页应提供普通用户的一键设备检查 | 已实现/部分真实 | “开始设备检查”调用 `/api/device/check`，按 STM32、RGB、DVP2 独立硬件域检查；STM32 未连接或缺 pyserial 不阻断 RGB/DVP2；RGB 状态来自 OpenCV/DirectShow adapter probe，多光谱来自 DVP2 SDK probe，标定显示需要确认 |
| 软件应支持统一设备发现、选择和绑定 | 部分实现 | P1B-3.6 新增基础层；P1B-3.7 完善 `/api/devices/discover`、`/api/devices/bindings`、`/api/devices/bind` 的角色/kind 校验、Windows RGB 身份元数据和相机设置页绑定入口。绑定保存 stableId 和 last known location，但不等于 connected/verified/ready |
| 软件不得把 COM 口或 camera index 当作永久身份 | 已实现/架构约束 | 串口 stableId 仅在 pyserial 提供 VID/PID/serial_number 时生成；COM 只保存为 `lastPort`。RGB 会尝试读取 Windows FriendlyName/PnP/VID/PID/USB serial，但 DirectShow index 或仅按顺序推断的映射只保存为 `lastDeviceIndex`/`mappingConfidence=inferred`，不生成 stableId。DVP2 优先按 serial/user id 匹配 |
| 设备发现阶段不得触发机械动作 | 已实现/测试覆盖 | 串口 discovery 对候选 COM 只执行 open/PING/close；已连接的正式 `SerialService` 端口标记 `inUse=true`，不二次打开；不调用风扇、门、光源、滤光轮或电机动作命令 |
| 相机服务层应与样品保存目录解耦 | 已实现 | `camera_service` adapter 返回 numpy 帧和状态，不决定 Sample Folder、文件名或 `rgbDirName/multispectralDirName` |
| RGB 相机帧色彩格式必须明确 | 已实现 | `RgbUvcCamera.capture_frame()` 把 OpenCV BGR 转为 RGB，返回 RGB `uint8` H×W×3 |
| RGB 相机状态必须区分检测、可用、打开、预览 | 已实现 | `CameraStatus` 暴露 `detected/available/opened/streaming`；probe 成功后释放句柄或停止预览不清空 `detected/available`；重新检测只使用当前配置的 device index，可兼容同 index 的两种 DirectShow 打开形式，不自动 fallback 到内置摄像头 |
| RGB 相机设置应区分保存配置和应用到真实相机 | 已实现 | 相机设置页提供“应用到相机”和“保存为默认配置”；`/api/camera/rgb/apply-settings` 下发参数并回读 actual 状态 |
| RGB 预览应与正式采集分离 | 已实现/部分真实 | `/api/camera/rgb/preview/start` 使用同一个 `RgbUvcCamera` 实例启动预览，`/api/camera/rgb/preview-frame` 返回 960x540 JPEG；`CameraManager.capture_rgb_frame()` 通过同一个 RGB adapter 取正式 RGB 帧，不使用预览 JPEG，不在预览运行时打开第二个 UVC 句柄；完整 `/api/capture/start` 仍未放行 |
| 多光谱相机接口必须支持未来 16-bit mono | 已实现/部分实机 | `CameraFrame` 不强制 `uint8`，允许 `uint16` H×W 单通道；DVP2 binding 的 `frame_to_array()` 会按 `dvpFrame.bits` 保留 `uint8` 或 `uint16`，预览 JPEG 才做显示归一化；用户当前实机验证为 `Mono8/uint8`，代码仍保留 `uint16` 边界 |
| 多光谱相机网页预览应读取真实 DVP2 当前流 | 已实现/需现场复核 | `/api/camera/multispectral/preview/start` 打开并保持同一个 DVP2 实例和 stream，`/api/camera/multispectral/preview-frame` 只读取当前流并输出 960x540 JPEG；预览响应包含 source dtype、PixelFormat 和亮度 min/max/mean |
| 多光谱网页预览应优先低延迟显示最新帧 | 已实现/需现场复核 | P1B-5.4 后 preview 使用后台 latest-frame cache，HTTP 请求只编码最新 cached frame，不为每个浏览器请求排队调用 DVP2 `get_frame()`；允许丢弃旧帧，响应和 UI 显示 frameId、sourceTimestamp、capture/resize/JPEG/server/browser fetch 耗时、measured FPS、drop 计数和 encoder；默认目标提升到 12 FPS，前端仍限制最多一个 in-flight request |
| 多光谱曝光/增益应能从网页真实下发并回读 | 已实现/需现场复核 | `/api/camera/multispectral/apply-settings` 调用 `Dvp2MonoCamera.set_exposure()` 和 `set_gain()`，回读实际值；曝光单位为 μs；范围来自 SDK capability，不在前端硬编码 |
| 多光谱 PixelFormat 本阶段只显示不切换 | 已实现 | 当前实际 `pixelFormat/frameDtype` 与 `supportedPixelFormats` 分开；只验证 `Mono8`，不开放格式切换 UI |
| DVP2 正式单帧保存必须保留 raw mono 位深 | 已实现/需实机复核 | `CameraManager.capture_multispectral_frame()` 返回 DVP2 `CameraFrame.data`，`CaptureCoordinator.run_multispectral_capture()` 只接受二维 MONO `uint8/uint16`，保存 PNG 前后读回验证尺寸、单通道和 dtype；不使用 960x540 预览 JPEG，不做 `astype(uint8)`、`/256` 或显示归一化 |
| DVP2 low-latency preview 不得影响 scientific capture | 已实现 | latest-frame cache 仅用于 `/api/camera/multispectral/preview-frame`；P1B-4 单帧和 P1B-5 多波段 sequence 仍通过 `capture_multispectral_frame()` 直接获取 raw `CameraFrame`，不读取 preview JPEG，不用 cache 冒充正式采集，不改变 uint8/uint16 raw PNG 保存和滤光轮同步 |
| DVP2 单帧未同步滤光轮时不得伪造波长 | 已实现 | P1B-4 metadata 固定记录 `wavelengthNm=null`、`bandAssignment=unassigned`、`filterWheelSynchronized=false`，`bands` 仍为空，不写假滤光轮位置 |
| DVP2 多波段 sequence 必须由滤光轮确认后采集 | 已实现/需实机验收 | P1B-5 `CaptureCoordinator.run_multispectral_sequence()` 会先 HOME，再按 enabled band 读取目标轮位、相对移动、查询确认位置、等待稳定、应用该 band 的 exposure/gain，之后才保存 DVP2 raw PNG；位置未知或不匹配会失败并 `safe_stop()`，不保存未同步 band |
| 多波段 metadata 必须记录真实 band plan 和部分完成状态 | 已实现 | P1B-5 metadata 写入 `bands`、`multispectralSequence`、enabled/disabled/completed/pending/failed bands、filter config source/version、developmentConfig、settlingMs、`partialCapture`、`cancelled` 和每帧 `wavelengthNm`/`bandAssignment`/`filterWheelSynchronized=true` |
| Dark/White reference 必须是 raw scientific 数据 | 已实现/需实机验收 | P1B-6 Dark/White reference 使用同一 DVP2 raw mono scientific saver，保存 `calibration/dark/band_XX_<bandId>.png` 与 `calibration/white/band_XX_<bandId>.png`，不使用 preview JPEG、不归一化、不把 `uint16` 转 `uint8`；每帧 metadata 明确 `captureType`、band、wavelength、filterWheel、requested/actual exposure/gain、min/max/mean/std |
| CalibrationSet 必须可追溯并可做兼容性检查 | 已实现 | P1B-6 写入 `calibration/calibration_set_<calibrationId>.json`，记录 camera identity、filter config、bands、dark/white frames、completed/missing bands、`calibrationComplete`、`sameBandSettingsMatched`；`validate_calibration_compatibility()` 比较 camera、filter config、band/wavelength、尺寸、dtype、PixelFormat、exposure/gain，并区分 compatible/warning/incompatible |
| 支持创建样品并保存元数据 | 已实现 | `/api/new-sample` 写 `metadata.json` |
| 每次样品创建应生成唯一保存目录 | 已实现 | `create_unique_sample_folder()` |
| 本次拍摄目录自动进入分析流程 | 部分实现/模拟 | 离线采集会设置 `analysisDataDir` |
| 用户可手动选择其他样品目录分析 | 已实现 | 主 UI 支持当前/其他文件夹 |
| 样品拍摄保存前应允许设置 RGB 与多光谱图像子目录名称 | 已实现 | 用户选择保存父目录后弹出“图像目录名称设置”；默认 `rgb`/`multispectral`，校验空值、非法字符、路径分隔符、`.`/`..` 和重名 |
| 手动读取样品时应先选择父文件夹，再选择 RGB/多光谱一级子目录 | 已实现 | `/api/inspect-image-folders` 扫描父目录直接子目录并建议角色；确认后 `/api/sample-folder` 使用实际选择的 `colorDir/multispectralDirName`；旧 `depthDir` 仅作为历史兼容 alias |
| 文件夹/文件路径选择应使用系统原生选择器，普通用户不需要手动输入完整 Windows 路径 | 已实现 | 主程序保存位置、其他样品文件夹、Model Studio 导入来源/样品文件夹和 labels.csv 均为只读路径显示 + 选择按钮；取消选择保留原路径 |
| 样品目录应包含 RGB、多光谱、校准目录 | 已实现/部分实现 | RGB/多光谱子目录名可配置并写入 `metadata.json.image_directories`；默认继续兼容 `rgb`/`multispectral`；P1B-3 受保护 RGB 单帧保存会写入 `<rgbDirName>/rgb_view_000.png`；P1B-4 受保护 DVP2 单帧保存会写入 `<multispectralDirName>/multispectral_frame_000.png`；P1B-5 受保护多波段 sequence 会写入 `<multispectralDirName>/band_XX_<bandId>.png`；P1B-6 受保护 Dark/White reference 会写入 `calibration/dark/band_XX_<bandId>.png`、`calibration/white/band_XX_<bandId>.png` 和 `calibration/calibration_set_<calibrationId>.json` |
| 样品采集应支持样品台多角度旋转拍摄设置 | 已实现/模拟 | 主程序可设置期望角度间隔、起始角度、方向和闭合补拍；后端生成 `captureRotationPlan` 并写入 metadata；当前样品台硬件为 simulated |
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
| Model Studio Dataset 应使用本地托管仓库，不直接依赖外部原始目录训练 | 已实现 | 样品导入会 COPY 到 `model_studio_data/datasets/<dataset_id>/samples/`，训练读取本地副本 |
| Model Studio 导入样品前应验证 RGB、多光谱、校准和 metadata | 已实现 | `validate_sample_folder()` 返回 Valid/Warning/Invalid，复用当前目录完整性规则并补充 `metadata.json` 检查 |
| Model Studio 重复导入样品不得静默覆盖 | 已实现 | 默认跳过重复样品，可选择作为新样品导入 |
| Model Studio 样品标签 SSC/TA/pH 应显式保存 | 已实现 | 标签输入是前端未保存状态，点击“保存标签”后才写 SQLite |
| 标签保存后同步维护 Dataset 本地 `labels.csv` | 已实现 | `save_sample_label()` 和 `import_labels()` 都会重写本地 `labels.csv` |
| Model Studio 数据准备页面应按 Workflow 展示操作顺序，避免按钮墙 | 已实现 | Dataset 页面按创建数据集、导入样品、标签录入、数据质量、创建版本组织；样品页以样品列表和选中样品 Ground Truth 为主 |
| 允许部分标签参与对应目标训练 | 已实现 | 空 SSC/TA/pH 保存为 NULL，样品标签状态显示 Missing/Partial/Complete |
| Dataset Version 应冻结样品和标签快照 | 已实现 | `sample_snapshot_json`/`label_snapshot_json` 记录版本创建时的本地路径和标签值 |
| 删除 Sample 时不得影响原始拍摄目录 | 已实现 | 可删除数据库记录，或二次确认后删除本地托管副本；不删除 `source_path` |
| Production/Default 模型必须人工发布 | 已实现 | `publish_model()` / `set_default_model()` |
| 系统不能自动替换正式模型 | 已实现 | 候选与发布目录隔离 |
| 支持不同水果/品种使用不同模型 | 已实现 | 模型目录和 SQLite 按 fruit_type/variety 过滤 |
| 支持 generic 品种模型兜底 | 已实现 | `model_catalog()` 和 `_select_registry_model()` |
| 普通检测用户不应默认看到 model_id、PLSR/SVR/RF、RAW/SNV/MSC 等高级模型细节 | 已实现 | 采集页新增“检测模型”摘要，默认显示 SSC/TA/pH 模型配置状态；点击“更换模型”后才展开原有模型下拉框 |
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
| Dataset 删除/归档策略 | Sample 删除已有基础实现；Dataset 级删除、归档和批量清理规则仍待确认 |
| 硬件通信协议最终格式 | 当前代码实现 zdyzzddy 两字节协议 `[CMD][PARAM] -> [CMD|0x80][RESULT]`；历史 `AA 55 CMD P1 P2 SUM` 格式是否废弃需确认 |
| DVP2 网页预览现场复核 | 用户已确认完全退出 BasedCam3 后 manual test 可打开和取帧；本轮 Codex 复测时当前运行环境 DVP2 枚举返回 0。需要在设备在线时从主程序相机设置页复核重新检测、打开预览、曝光/增益应用、停止/重启预览 |
| STM32 设备身份命令 | 当前固件协议只有 PING 等两字节命令；PING 只能证明兼容协议，不能区分 MAIN_CONTROLLER 或 ROTATION_CONTROLLER。下一版建议增加 GET_DEVICE_TYPE / GET_DEVICE_INFO，返回 deviceType、deviceId、firmwareVersion 和 capabilities |
| 是否保留网页局域网访问 | 当前启动本地 127.0.0.1；远程访问和权限待定 |

## 未实现但重要的需求

| 需求 | 备注 |
| --- | --- |
| 真实黑白相机多波段采集 | DO3THINK/度申 GigE/RJ45 黑白相机已完成 DVP2 adapter、manual test 用户实机通过、网页预览/参数控制 API、Coordinator 受保护 raw mono 单帧保存、受保护滤光轮+DVP2 多波段 sample sequence 和 Dark/White calibration sequence 软件路径；下一步需要真实滤光轮现场验收、暗白物理校正验收、样品旋转和完整 `/api/capture/start` 闭环；不能用 Wi-Fi link 或 OpenCV VideoCapture 替代 |
| STM32 串口通信 | 已有两字节协议、超时、状态查询和测试；仍需实机验证和采集编排 |
| 滤光轮 HOME/GOTO/报警 | 已有 HOME、相对旋转、位置查询、故障码查询；P1B-5 软件 sequence 已用 HOME/相对旋转/位置查询完成 band 同步；绝对 GOTO、最终 STM32 contract 和真实硬件验收待补 |
| LED 光源开关和亮度控制 | 已有 RGB LED/钨灯开关命令；亮度控制、三路 LED 是否需要扩展需硬件确认 |
| 门控、急停、温度、电机报警状态 | 升降门、急停、故障码已有基础 API；温度和扩展报警未接入 |
| RGB 与多光谱标定配准 | `calibrated` registration 未实现 |
| 正式 Production 模型 | 当前仓库没有模型文件和真实训练数据 |
| 检测历史记录数据库 | 当前只有 Model Studio SQLite，不保存检测结果历史 |
| 正式报告导出 | 当前前端导出 TXT |
