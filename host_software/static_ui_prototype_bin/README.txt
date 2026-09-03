水果糖酸度分析软件 - 上位机原型

运行方式
1. 开发运行: python launcher.py
2. 打包运行: dist/FruitTasteAnalyzer.exe

当前架构
- launcher.py: 启动本地 Python HTTP 服务，释放打包内置网页资源，并打开浏览器界面。
- backend_server.py: 提供静态页面、JSON API、样品会话、目录选择、任务队列、进度轮询、离线采集验证和结果文件服务。
- camera_service/: RGB UVC 相机和 DO3THINK/DVP2 多光谱黑白相机接入层；adapter 只负责设备状态、参数和帧，不直接决定样品目录或文件命名。
- serial_service.py / hardware_controller.py / device_manager.py: STM32 串口、基础硬件控制和设备状态管理；在完整真实采集协调器接入前，/api/capture/start 仍受 CameraIntegrationRequired 保护。
- pointcloud_service.py: 当前优先执行 RGB + multispectral 二维形态与表面分析，兼容旧 RGB-D/PLY 点云流程。
- pipeline_v2.py: 旧 RGB-D/SFM 点云重建工具函数，作为兼容路径保留。
- quality_algorithm/、training/、quality_prediction.py: 多光谱校正、ROI、特征提取、RAW/SNV/MSC 预处理、PLSR/SVR/RF 训练和 SSC/TA/pH 预测入口。
- model_studio/: 数据集、样品、本地托管、标签、特征、训练实验、候选模型、Published/Default 模型管理。
- index.html / styles.css / app.js: 主工作站界面、设备准备、相机设置、采集/分析状态流转和 API 调用。

当前硬件接入状态
- RGB 彩色相机已通过 camera_service/RgbUvcCamera 使用 OpenCV/DirectShow 接入。当前电脑已验证 device_index=1、MJPG、3840x2160、25fps，可进行设备探测、预览、参数应用，并显示 requested/actual 状态；device_index 只是当前配置默认值，不是跨电脑固定设备身份。RGB 尚未接入正式真实采集保存流程。
- 多光谱黑白相机为 DO3THINK/度申 DVP2 GigE/RJ45 相机，已通过 Dvp2MonoCamera -> Dvp2Binding -> ctypes -> DVPCamera64.dll 接入。当前代码支持 DVP2 SDK 查找、设备枚举、按设备标识匹配、open/close、stream、capture_frame、曝光、增益、preview 和 frame metadata；用户实机 manual test 已验证 Mono8、2048x1200、uint8 取帧和 PNG 保存。正式多波段采集保存尚未接入。
- STM32 / 硬件控制层已存在真实 serial_service.py、hardware_controller.py、device_manager.py，支持串口连接、PING、风扇、升降门、RGB LED、钨灯、滤光轮 HOME / 相对旋转、急停、fault clear 和基础安全 interlock。完整真实设备流程仍需要进一步实机联调。
- 样品旋转平台当前已有 sample_rotation 角度计划、metadata 和 views.json 记录；真实样品台电机控制尚未接入。sample_rotation 与 filter_wheel_rotation 是两个独立控制对象。

当前采集状态
- RGB / DVP2 可以真实预览，STM32 基础控制可以真实执行。
- 当前还没有完整 CaptureCoordinator，因此 RGB + DVP2 + 光源 + 滤光轮 + 样品旋转 + 图片保存 + metadata 尚未形成完整真实采集闭环。
- /api/capture/start 仍然受到 CameraIntegrationRequired 保护，trueCapturePrepared 仍保持 false。
- 当前“完成采集”的离线验证路径仍会调用 create_offline_capture_dataset()，用于生成可分析的本地验证图像、校准目录和 metadata；这不是正式真实相机采集。

样品数据与分析流程
1. 用户完成设备准备后创建样品，系统创建样品目录、RGB 子目录、多光谱子目录、calibration/dark、calibration/white 和 metadata.json。
2. RGB 和多光谱子目录默认分别为 rgb、multispectral，也可在保存前配置，实际名称写入 metadata.json.image_directories。
3. 完成采集时当前走离线验证函数 create_offline_capture_dataset() 写入测试图像；本次拍摄目录会自动进入分析流程。
4. 形态分析优先读取 RGB + multispectral 数据，输出面积、宽度、高度、果粉覆盖率、颜色均匀度和多光谱统计等结果。
5. RGB-D / PLY 点云流程仍作为兼容支持保留；三维尺寸和体积仍需要进一步真实标定和算法验证。

糖酸模型状态
- 当前不是纯前端手动假功能。项目已经具备真实软件算法链：quality_algorithm/、training/、quality_prediction.py 和 model_studio/。
- 已支持 Dark/White 校正、RGB ROI、多波段特征、RAW/SNV/MSC、PLSR/SVR/RF、SSC/TA/pH PredictionResult，以及 Model Studio 中的 Candidate / Published / Default 模型管理。
- 当前仓库没有真实正式 Production 模型和足够真实实验数据。缺少兼容模型时预测必须返回 model_missing，不能生成假结果。
- 糖酸比和口感等级由有效 SSC / TA 预测结果推导；缺少预测值时前端会提示等待数据。

说明
- 当前项目主数据结构已经从早期 RGB-D 示例转向 RGB + multispectral 两相机样品目录；旧 RGB-D/PLY 能力仅作为兼容路径存在。
- sample_data 不再作为随程序内置的正式 demo 图像数据来源；离线验证数据由 create_offline_capture_dataset() 按当前样品目录生成。
- EXE 为单文件打包，内置前端资源；移动到其他 Windows 电脑后仍需按目标机器重新确认相机 device_index、DVP2 SDK、串口和硬件连接。
- 当前形态分析已有真实图像/点云算法实现，不使用固定数值冒充结果；但尺寸、体积和三维结果仍需真实标定后才能作为正式测量。

测试
python -X utf8 -m unittest discover -s tests -v

打包
pyinstaller --clean --noconfirm FruitTasteAnalyzer.spec
