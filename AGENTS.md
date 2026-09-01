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
