# Project Status

更新时间：2026-09-04

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

- RGB 彩色相机：已通过 OpenCV/DirectShow `RgbUvcCamera` 接入，当前电脑验证默认 `device_index=1`、`MJPG`、`3840x2160`、`25fps`，可预览和应用参数；尚未接入正式采集保存。
- 多光谱黑白相机：DO3THINK/度申 MGV231M-H2 GigE/RJ45，已基于 DVP2 SDK `ctypes` 接入 adapter；用户已确认退出 BasedCam3 后 manual test 可打开、取流、30 帧取图和保存 PNG；主 UI 已接入 DVP2 重新检测、网页预览、曝光/增益下发和回读；尚未接入正式多波段采集保存。
- STM32/串口：已有两字节协议、串口连接、PING、急停、故障清除、风扇、门、光源和滤光轮基础控制层；仍需完整实机流程联调。
- 滤光片转轮：已有 HOME/相对旋转控制入口；尚未和多光谱相机、光源、样品旋转组成 CaptureCoordinator。
- 样品旋转平台：已有软件角度计划和 metadata/views 记录；真实样品台电机未接入。
- 标定配准：暗/白校正算法已有；RGB 与多光谱几何配准仍未完成。

## 当前采集流程

当前主流程仍是离线验证流程，不是真实完整采集。用户完成设备准备后创建样品，系统创建样品目录和 metadata；完成采集时仍调用 `create_offline_capture_dataset()` 生成测试图像。真实相机预览已可用，但 `/api/capture/start` 仍由 `CameraIntegrationRequired` 保护，`trueCapturePrepared` 保持 `false`。

## 当前 RGB / 多光谱目录逻辑

保存时用户先选择保存父目录，再确认 RGB 与多光谱图像子目录名称。默认兼容旧结构：

- RGB：`rgb`
- 多光谱：`multispectral`

用户可改成自定义目录名，实际名称会写入 `metadata.json.image_directories`。本次拍摄分析优先使用 session/metadata 中的实际目录名；手动分析其他样品时先选择父目录，再扫描一级子目录，由用户选择 RGB、多光谱和其他目录。

## 当前模型训练状态

Model Studio 已支持 Dataset、本地托管样品、`labels.csv`/手动标签、数据质量检查、Dataset Version、特征提取、PLSR/SVR/RF、RAW/SNV/MSC、候选模型、Published/Default 发布。主程序可按水果/品种/target 选择 Default 或 generic fallback 模型。当前仓库没有可直接上线的真实 Production 模型，缺模型时预测返回 `model_missing`，不会生成假数值。

## 当前 UI 状态

主程序是静态 HTML/CSS/JS + Python 本地 HTTP 服务。已有统一系统状态、一键设备检查、采集/分析布局切换、相机设置页、形态分析、SSC/TA/pH 分析入口、糖酸比口感分析和 Model Studio。相机设置页已有 RGB 与多光谱分栏，支持预览、参数应用和设备详情；正式采集工作流仍未改造成真实硬件协调流程。

## 当前开发重点

下一阶段重点应是 CaptureCoordinator：把 RGB 取帧、DVP2 多光谱取帧、滤光片转轮、光源、样品旋转、目录保存和 metadata/views 写入接成真实采集闭环。需要继续保持 `devicePrepared` 与 `trueCapturePrepared` 的语义区分，不能因为预览可用就放行完整真实采集。

## 已知问题

- 正式真实采集保存未实现，当前完成采集仍是离线验证数据。
- DVP2 网页预览需要在设备在线且 BasedCam3 完全退出后做现场复核；Codex 本轮复测时当前环境枚举返回 0。
- 多光谱 PixelFormat 当前只验证 `Mono8`，不开放切换。
- 滤光轮、光源、相机曝光、样品旋转尚未形成同步采集编排。
- RGB 与多光谱几何配准、尺寸标定未完成。
- 当前没有真实训练数据和正式 Production 模型。
- 检测历史数据库和正式报告导出未实现。
