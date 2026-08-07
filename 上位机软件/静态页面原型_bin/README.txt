水果糖酸度分析软件 - 上位机原型

运行方式
1. 开发运行: python launcher.py
2. 打包运行: dist/FruitTasteAnalyzer.exe

当前架构
- launcher.py: 启动本地 Python HTTP 服务，释放打包内置网页资源，并打开浏览器界面。
- backend_server.py: 提供静态页面、本地数据集上传、任务队列、进度轮询、任务取消和结果文件服务。
- pointcloud_service.py: 提供 RGB-D 点云形态分析服务层，调用 pipeline_v2 点云重建流程，返回平均深度、直径、高度、体积、估重和预览图。
- pipeline_v2.py: OpenCV/SFM 点云重建流程。
- index.html / styles.css / app.js: 商业化任务树界面、前端状态流转和 API 调用。

真实点云闭环
1. 前端点击“选择数据集”后，浏览器选择 RGB-D 文件夹，并通过 /api/upload-dataset 上传到本地缓存。
2. 也可以不选择数据，直接使用随程序打包的 sample_data/rgbd_sample_object 示例数据。
3. 点击“开始形态分析”后，app.js 调用 POST /api/analyze-shape。
4. backend_server.py 创建后台任务，避免界面卡死。
5. pointcloud_service.py 调用 pipeline_v2.py，读取 RGB 图和深度图，进行 OpenCV 掩膜分割、SFM 位姿估计、深度补洞、异常点处理、点云融合、纹理采样，估算体积和重量，并生成点云预览图。
6. app.js 轮询 /api/jobs/{jobId}，实时显示步骤、进度、运行时间、日志和结果。
7. 分析成功后，前端加载后端生成的 .ply 点云文件，可用鼠标拖拽旋转，滚轮缩放，点击“重置视角”恢复默认角度。

说明
- 当前单片机、真实相机、电机、LED 光源和滤光轮仍未接入，相关页面先作为自检/设置预留。
- 糖度、酸度、口感评级保留为前端手动/离线功能，后续可替换为模型 API 或硬件采集结果。
- EXE 为单文件打包，内置前端资源和示例 RGB-D 数据；移动到其他 Windows 电脑后可直接双击运行。
- 当前点云流程使用 OpenCV/SFM 点云重建算法，没有用固定数值冒充结果。
- sample_data/rgbd_sample_object 是处理后的样品对象示例，默认用于演示。
- sample_data/rgbd_grape 是包含绿幕、桌面和样品的原始 RGB-D 场景，保留用于算法调试。

测试
python -X utf8 -m unittest discover -s tests -v

打包
pyinstaller --clean --noconfirm FruitTasteAnalyzer.spec
