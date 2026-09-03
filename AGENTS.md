# AGENTS.md

这是果实口感多光谱无损检测系统项目。当前主体在 `host_software/static_ui_prototype_bin/`，用 Python 本地 HTTP 后端、静态 HTML/CSS/JS 前端、PyInstaller 打包，并包含 Model Studio、光谱特征提取、PLSR/SVR/RF 训练和 SSC/TA/pH 预测入口。

每次进入项目，先读：

1. `docs/PROJECT_CONTEXT.md`
2. `docs/REQUIREMENTS.md`
3. `docs/ARCHITECTURE.md`
4. `docs/CHANGELOG.md`

开发规则：

- 修改代码前先确认当前真实实现，不要只根据旧聊天或旧 README 判断。
- 不允许为了新功能随意推翻现有 UI；优先在现有界面和流程上小范围演进。
- 优先最小范围修改，避免无关重构和格式 churn。
- 文件夹/文件路径输入优先使用系统原生选择器和只读路径显示，避免要求普通用户手动输入完整 Windows 路径。
- 不允许把 mock、离线模拟、占位按钮写成真实功能。
- 不允许虚构已经接入的硬件、相机 SDK、STM32 串口、光源、电机或生产模型。
- 根目录 `camera/` 是厂商资料目录，不要改名、移动、自动解压或当成 Python package；相机接入代码放在 `host_software/static_ui_prototype_bin/camera_service/`。
- P1A-RGB Finalize 后 RGB 相机已有 OpenCV/DirectShow `RgbUvcCamera` adapter，并已在当前电脑验证 `device_index=1`、`MJPG`、`3840x2160`、`25fps`、RGB `uint8` 取帧；该 index 只能作为配置默认值，不要在 adapter 内硬编码为跨电脑身份。
- RGB 相机状态语义固定为：`detected`=最近一次真实 probe 找到当前配置设备，`available`=当前配置可打开并取帧，`opened`=当前 `VideoCapture` 句柄保持打开，`streaming`=预览正在运行；probe/preview stop 释放句柄后 `opened=false`，但不能把已验证的 `detected/available` 清成 false。
- `RgbUvcCamera` 可以为同一个逻辑 `device_index` 兼容 `VideoCapture(index, CAP_DSHOW)` 和 `VideoCapture(index + CAP_DSHOW)` 两种 OpenCV DirectShow 打开形式；这不是 fallback 到其他摄像头，禁止自动扫描或静默改用 index=0。
- Camera Settings UX/P1A-2B 阶段后，相机设置页已有 RGB 和 DVP2 多光谱的参数应用、回读和 960x540 JPEG 预览；这仍不等于正式采集，`trueCapturePrepared` 必须保持 false，`DeviceManager.start_capture()` 的 `CameraIntegrationRequired` 保护不能移除。
- DVP2 多光谱相机是 DO3THINK/度申 GigE/RJ45 工业黑白相机。P1A-2B 后已基于 `D:\Netease\DVP2 SDK CN` 的真实 `DVPCamera.h`/官方示例新增 Python 3.12 `ctypes` binding；目标设备信息为 `MGV231M-H2-169.254.25.110`、`UserID=GP23400004963`、SDK serial `DSGP23400004963`、MAC `B4-61-D3-14-6E-18`。用户已确认完全退出 BasedCam3 后 manual test 可打开、读参数、开始取流、30 帧取图和 PNG 保存，实际帧 `2048x1200`、`Mono8`、`uint8`。相机设置页已接入 DVP2 重新检测、网页预览、曝光/增益真实下发与回读；但正式多波段采集保存和 CaptureCoordinator 仍未接入。不要用 OpenCV VideoCapture、不要根据 Wi-Fi 或普通网卡 link 伪造连接/帧、不要把预览成功写成完整真实采集成功。
- 真实优先级固定为：当前代码 > 当前配置/数据库结构 > 当前测试 > 最新项目文档 > 历史项目文档 > 历史聊天上下文。
- `create_offline_capture_dataset()` 是离线验证函数，不是真实采集。
- `sample_rotation` 是样品台/水果多视角旋转；`filter_wheel_rotation` 是滤光片转轮切换多光谱波段。两者是独立控制对象，不允许混用角度、状态或电机逻辑。
- `trained_models/<target>/` 只有存在 `model.joblib` 和 `metadata.json` 时才代表可用默认模型。
- Production/Default 模型必须通过 Model Studio 人工发布，不能由训练任务自动替换。
- 支持不同水果/品种/指标使用不同模型；注意 `generic` 品种兜底。
- 新增或修改重要架构后，同步更新 `docs/PROJECT_CONTEXT.md` 和 `docs/ARCHITECTURE.md`。
- 新增重大需求后，同步更新 `docs/REQUIREMENTS.md`。
- 完成重大功能后，同步更新 `docs/CHANGELOG.md`。
- 测试优先使用 `python -X utf8 -m unittest discover -s tests -v`，工作目录为 `host_software/static_ui_prototype_bin/`。
