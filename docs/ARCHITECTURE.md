# Architecture

更新时间：2026-09-02

## 总体结构

```text
host_software/static_ui_prototype_bin/
  launcher.py
  backend_server.py
  index.html / styles.css / app.js
  serial_service.py
  hardware_controller.py
  device_manager.py
  pointcloud_service.py
  pipeline_v2.py
  rotation_plan.py
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
| 设备准备状态 | `backend_server.py`, `app.js` | `SessionState.update_device_preparation()`, `requireDevicePreparation()` | 连接/电机/光源/相机/标定检查状态 | `/api/device-preparation`，`devicePrepared` 表示当前离线验证可用，`trueCapturePrepared` 表示未来真实采集门槛 | 串口/滤光轮部分可走真实 API，相机和样品台仍为离线/待接入 |
| 全局系统状态 | `app.js` | `deriveSystemStatus()`, `renderSystemStatus()` | `state`、设备状态、样品状态、形态任务、预测任务 | 顶栏“当前状态” | 前端派生状态，不新增后端状态源 |
| STM32 串口 | `serial_service.py` | `SerialService` | CMD/PARAM 两字节命令、串口名、超时 | RESULT、异常 | pyserial |
| 硬件控制 | `hardware_controller.py` | `HardwareController` | 风扇、升降门、RGB LED、钨灯、滤光轮、急停、故障清除 | 两字节命令和状态查询 | SerialService |
| 设备管理 | `device_manager.py`, `backend_server.py` | `DeviceManager`, `DeviceManager.self_test()` | 串口连接、自检、状态、急停、采集状态 | `/api/device/*`, `/api/capture/*`，self-test `checks` | HardwareController |
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
  -> SessionState.snapshot()
  -> defaultSaveRoot
```

输出包括 Python 依赖、设备准备状态、真实/离线设备状态、当前样品、当前拍摄目录、分析目录、果种/品种、已选模型。

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
  -> STM32 two-byte protocol

app.js runUnifiedDeviceCheck()
  -> GET /api/device/status
  -> POST /api/device/self-test {includeMotion: true}
  -> DeviceManager.self_test()
  -> checks: controller/door/fan/filterWheel/rgbCamera/multispectralCamera/light/calibration

app.js runDeviceTest()/confirmCalibrationCheck()
  -> POST /api/device-preparation
  -> SessionState.update_device_preparation()
  -> SessionState.devicePrepared = all(connect, motor, light)
  -> SessionState.trueCapturePrepared = all(connect, motor, light, camera, calibration)
```

串口连接、STM32 PING、风扇开启、滤光轮寻零、升降门/输出状态查询、急停和故障清除已有真实 API。`DeviceManager.self_test()` 的 `checks.rgbCamera` 和 `checks.multispectralCamera` 当前固定为 `not_connected`，标定为 `manual_required`。相机 SDK、样品台旋转电机和完整真实采集编排仍未接入，因此 `/api/capture/start` 在相机服务接入前返回 409。`/api/new-sample` 和 `/api/complete-capture` 仍会通过 `require_device_preparation()` 阻止未完成当前离线设备准备时开始样品流程。

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
  -> GET /api/sample-folder?datasetDir=<parent>&colorDir=<rgbDirName>&depthDir=<multispectralDirName>&strictImageDirs=1

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

- `datasets`：`dataset_id`、`dataset_name`、`fruit_type`、`variety`、`storage_path`、`local_path`、`import_source_path`、`dirty`、`latest_version_id`。
- `dataset_versions`：`sample_ids`、`sample_snapshot_json`、`label_snapshot_json`、`snapshot_hash`，用于冻结版本样品和标签。
- `samples`：`sample_id`、`sample_name`、`source_path`、`local_path`、`storage_path`、RGB/多光谱/校准计数、标签镜像、状态字段。
- `labels`：SQLite 标签权威来源，保存 `sample_id`、`ssc`、`ta`、`ph`、`updated_at`。
- `training_experiments`
- `jobs`
- `models`
- `operation_logs`

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

Sample delete action
  -> delete_sample()
  -> delete labels + sample row
  -> optional delete managed local copy under Dataset samples/
  -> never delete samples.source_path

Dataset local storage
  -> import_labels()
  -> create_dataset_version()
  -> sample_snapshot_json + label_snapshot_json
  -> generate_features()
  -> model_studio/artifacts/features/<dataset_version>_features.csv
  -> create_training_job()
  -> training.train_one()
  -> model_studio/models/candidates/<experiment>/<target>_<pre>_<model>/
  -> models.status = Candidate
  -> publish_model()
  -> trained_models/published/<model_id>/
  -> optional setDefault
  -> trained_models/<target>/
```

模型发布约束：

- Candidate 不会自动进入主程序。
- Published 可被选择，但 Default 才会作为对应 fruit_type/variety/target 的自动默认。
- `trained_models/<target>/` 是 legacy/default fallback，不代表所有已发布模型。

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
