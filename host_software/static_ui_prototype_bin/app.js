const state = {
  ssc: null,
  ta: null,
  ph: null,
  ratio: null,
  grade: null,
  captureStep: 0,
  shapeJobId: null,
  shapeTimer: null,
  shapeStartedAt: null,
  shapeDone: false,
  systemTask: "",
  deviceCheckRunning: false,
  deviceChecks: {},
  deviceCheckDetail: null,
  shapeMode: "morphology2d",
  saveRootDir: "",
  currentCaptureDir: "",
  currentCaptureValid: false,
  analysisDataDir: "",
  rgbDirName: "rgb",
  multispectralDirName: "multispectral",
  otherImageDirs: [],
  captureStarted: false,
  calibrationStatus: "pending",
  devicePrep: {
    connect: false,
    motor: false,
    light: false,
    camera: false,
    calibration: false,
  },
  hardwareStatus: {
    connected: false,
    port: "",
    fanOn: false,
    door: "unknown",
    wheelPosition: null,
    wheelHomed: false,
    rgbLed1On: false,
    rgbLed2On: false,
    tungsten1On: false,
    tungsten2On: false,
    errorCode: null,
    emergencyStopped: false,
  },
  cameraStatus: {
    rgb: null,
    multispectral: null,
  },
  cameraSettingsTab: "rgb",
  rgbPreviewRunning: false,
  rgbPreviewFetching: false,
  rgbPreviewTimer: null,
  rgbPreviewFrameUrl: "",
  multispectralPreviewRunning: false,
  multispectralPreviewFetching: false,
  multispectralPreviewTimer: null,
  multispectralPreviewFrameUrl: "",
  serialPorts: [],
  dataSource: "other",
  captureCompleting: false,
  captureRotationPlan: null,
  hasSample: false,
  sampleName: "",
  sampleId: "",
  sampleCreatedAt: "",
  fruitType: "",
  variety: "generic",
  selectedSscModelId: "",
  selectedTaModelId: "",
  selectedPhModelId: "",
  modelAdvanced: false,
  modelCatalog: null,
  sampleSession: {
    sampleId: "",
    sampleName: "",
    analysisDataDir: "",
    rgbFiles: [],
    multispectralFiles: [],
    captureTime: "",
    fruitType: "",
    variety: "generic",
    selectedSscModelId: "",
    selectedTaModelId: "",
    selectedPhModelId: "",
    sscResult: null,
    taResult: null,
    phResult: null,
  },
  dataCheck: {
    status: "empty",
    rgbCount: 0,
    spectralCount: 0,
    pairCount: 0,
  },
  viewer: null,
  imageBrowser: {
    images: [],
    index: 0,
  },
};

const CAMERA_SETTINGS_KEY = "fruitAnalyzer.cameraSettings";
const DEFAULT_CAMERA_SETTINGS = {
  deviceIndex: 1,
  width: 3840,
  height: 2160,
  resolution: "3840 x 2160",
  fps: 25,
  fourcc: "MJPG",
  autoExposureEnabled: true,
  exposure: -5,
  gainAuto: true,
  gain: 1,
  autoWhiteBalanceEnabled: true,
  whiteBalance: 4600,
  fx: 652.77,
  fy: 652.77,
  cx: 631.75,
  cy: 364.95,
};

const DEFAULT_ROTATION_SETTINGS = {
  enabled: false,
  expectedIntervalDeg: 30,
  startAngleDeg: 0,
  direction: "CW",
  includeClosureView: false,
};

const titles = {
  motor: "设备准备",
  light: "光源自检",
  camera: "相机自检",
  "reserved-1": "预留功能",
  "reserved-2": "预留功能",
  capture: "样品采集",
  shape: "形态分析",
  sugar: "糖度预测",
  acid: "酸度与 pH 分析",
  taste: "口感评级",
  "camera-settings": "相机设置",
  "light-settings": "光源设置",
  "reserved-3": "算法参数",
  "reserved-4": "通信设置",
};

const moduleLayoutModes = {
  motor: "capture",
  light: "capture",
  camera: "capture",
  capture: "capture",
  "camera-settings": "capture",
  "light-settings": "capture",
  "reserved-1": "capture",
  "reserved-2": "capture",
  "reserved-3": "capture",
  "reserved-4": "capture",
  shape: "analysis",
  sugar: "analysis",
  acid: "analysis",
  taste: "analysis",
};

const shapeStepMap = {
  check: "load-rgbd",
  preprocess: "preprocess",
  images: "image-review",
  filter: "filter",
  texture: "surface-texture",
  measure: "measure",
  preview: "volume",
  done: "confirm",
};

const deviceCheckOrder = [
  "controller",
  "door",
  "fan",
  "filterWheel",
  "rgbCamera",
  "multispectralCamera",
  "light",
  "calibration",
];

const deviceCheckLabels = {
  controller: "控制器",
  door: "升降门",
  fan: "风扇",
  filterWheel: "滤光轮",
  rgbCamera: "RGB 相机",
  multispectralCamera: "多光谱相机",
  light: "光源控制",
  calibration: "标定",
};

const checkStatusText = {
  pending: "待检查",
  checking: "检查中",
  passed: "正常",
  warning: "需注意",
  failed: "失败",
  not_connected: "尚未接入",
  sdk_missing: "SDK 未安装",
  unsupported: "暂不支持",
  manual_required: "需要确认",
};

function $(selector) {
  return document.querySelector(selector);
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function setInputValueUnlessFocused(id, value) {
  const node = document.getElementById(id);
  if (!node || document.activeElement === node) return;
  node.value = value;
}

function setPreviewImage(imageSelector, emptySelector, src = "") {
  const image = $(imageSelector);
  const empty = $(emptySelector);
  if (!image) return;
  if (src) {
    image.src = src;
    image.hidden = false;
    if (empty) empty.hidden = true;
  } else {
    image.removeAttribute("src");
    image.hidden = true;
    if (empty) empty.hidden = false;
  }
}

function escapeHtml(value = "") {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function parseNumberSetting(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseResolutionSetting(value, fallback = DEFAULT_CAMERA_SETTINGS) {
  if (typeof value === "string") {
    const match = value.replace(/\s+/g, "").match(/^(\d+)[x×](\d+)$/i);
    if (match) {
      return {
        width: Number(match[1]),
        height: Number(match[2]),
        resolution: `${Number(match[1])} x ${Number(match[2])}`,
      };
    }
  }
  const width = parseNumberSetting(value?.width, fallback.width);
  const height = parseNumberSetting(value?.height, fallback.height);
  return { width, height, resolution: `${width} x ${height}` };
}

function legacyAutoEnabled(value, fallback = true) {
  if (typeof value === "boolean") return value;
  if (typeof value !== "string") return fallback;
  return ["auto", "自动", "on", "true"].includes(value.trim().toLowerCase());
}

function normalizeAngleDeg(value) {
  const normalized = ((Number(value) % 360) + 360) % 360;
  return Math.abs(normalized) < 1e-9 ? 0 : Number(normalized.toFixed(6));
}

function formatAngleDeg(value) {
  const rounded = Number(Number(value).toFixed(6));
  return Number.isInteger(rounded) ? String(rounded) : String(rounded);
}

function viewToken(angle, closure = false) {
  if (closure) return "360";
  const text = formatAngleDeg(normalizeAngleDeg(angle)).replace(".", "p");
  return /^\d+$/.test(text) ? String(Number(text)).padStart(3, "0") : text;
}

function readRotationSettingsFromForm() {
  return {
    enabled: Boolean($("#multiViewEnabled")?.checked),
    expectedIntervalDeg: parseNumberSetting($("#rotationIntervalDeg")?.value, DEFAULT_ROTATION_SETTINGS.expectedIntervalDeg),
    startAngleDeg: parseNumberSetting($("#rotationStartAngleDeg")?.value, DEFAULT_ROTATION_SETTINGS.startAngleDeg),
    direction: $("#rotationDirection")?.value === "CCW" ? "CCW" : "CW",
    includeClosureView: Boolean($("#includeClosureView")?.checked),
  };
}

function applyRotationPlanToForm(plan = {}) {
  if ($("#multiViewEnabled")) $("#multiViewEnabled").checked = Boolean(plan.enabled);
  if ($("#rotationIntervalDeg") && Number.isFinite(Number(plan.expected_interval_deg))) {
    $("#rotationIntervalDeg").value = plan.enabled ? formatAngleDeg(plan.expected_interval_deg) : DEFAULT_ROTATION_SETTINGS.expectedIntervalDeg;
  }
  if ($("#rotationStartAngleDeg") && Number.isFinite(Number(plan.start_angle_deg))) {
    $("#rotationStartAngleDeg").value = formatAngleDeg(plan.start_angle_deg);
  }
  if ($("#rotationDirection")) $("#rotationDirection").value = plan.direction === "CCW" ? "CCW" : "CW";
  if ($("#includeClosureView")) $("#includeClosureView").checked = Boolean(plan.include_closure_view);
}

function buildCaptureRotationPlan(settings = readRotationSettingsFromForm()) {
  const enabled = Boolean(settings.enabled);
  const direction = settings.direction === "CCW" ? "CCW" : "CW";
  const startAngleDeg = normalizeAngleDeg(settings.startAngleDeg || 0);
  const includeClosureView = Boolean(enabled && settings.includeClosureView);
  let expectedIntervalDeg = Number(settings.expectedIntervalDeg);
  if (!enabled) {
    expectedIntervalDeg = 360;
  } else if (!Number.isFinite(expectedIntervalDeg) || expectedIntervalDeg <= 0) {
    throw new Error("期望角度间隔必须大于 0。");
  }
  const viewCount = !enabled || expectedIntervalDeg >= 360 ? 1 : Math.ceil(360 / expectedIntervalDeg);
  const actualIntervalDeg = 360 / viewCount;
  const normalAngles = Array.from({ length: viewCount }, (_, index) => normalizeAngleDeg(startAngleDeg + index * actualIntervalDeg));
  const angles = [...normalAngles];
  if (includeClosureView) angles.push(Number((startAngleDeg + 360).toFixed(6)));
  const views = angles.map((angle, index) => {
    const closure = includeClosureView && index === angles.length - 1;
    return {
      view_id: `view_${viewToken(angle, closure)}`,
      logical_angle_deg: normalizeAngleDeg(angle),
      mechanical_angle_deg: closure ? Number((startAngleDeg + 360).toFixed(6)) : normalizeAngleDeg(angle),
      capture_order: index + 1,
      direction,
      closure_view: closure,
      sample_rotation_control: "sample_stage",
      filter_wheel_control: "independent",
    };
  });
  return {
    enabled,
    sample_rotation_hardware: "simulated",
    expected_interval_deg: Number(expectedIntervalDeg.toFixed(6)),
    view_count: viewCount,
    total_capture_views: views.length,
    actual_interval_deg: Number(actualIntervalDeg.toFixed(6)),
    start_angle_deg: startAngleDeg,
    direction,
    include_closure_view: includeClosureView,
    angles_deg: angles,
    normal_angles_deg: normalAngles,
    closure_angle_deg: includeClosureView ? Number((startAngleDeg + 360).toFixed(6)) : null,
    returned_home: false,
    home_status: "PENDING",
    completed_views: [],
    pending_views: views.map((view) => view.view_id),
    failed_view: "",
    rotation_domain: "sample_rotation",
    filter_wheel_rotation_independent: true,
    views,
  };
}

function renderRotationPlan(plan = null) {
  if (plan) applyRotationPlanToForm(plan);
  const fields = $("#rotationSettingsFields");
  const card = $("#rotationCaptureCard");
  const enabled = Boolean($("#multiViewEnabled")?.checked);
  if (fields) fields.hidden = !enabled;
  try {
    const nextPlan = plan || buildCaptureRotationPlan();
    state.captureRotationPlan = nextPlan;
    if (card) card.dataset.status = "ok";
    setText("rotationPlanStatus", nextPlan.enabled ? "多角度" : "单视角");
    setText("rotationViewCount", `${nextPlan.total_capture_views || nextPlan.view_count} 个视角`);
    setText("rotationActualInterval", nextPlan.enabled ? `${formatAngleDeg(nextPlan.actual_interval_deg)}°` : "--");
    setText("rotationAngles", (nextPlan.angles_deg || [0]).map((angle) => `${formatAngleDeg(angle)}°`).join(" · "));
    renderCaptureRotationStatus(nextPlan);
    return nextPlan;
  } catch (error) {
    if (card) card.dataset.status = "invalid";
    setText("rotationPlanStatus", "设置无效");
    setText("rotationViewCount", "--");
    setText("rotationActualInterval", "--");
    setText("rotationAngles", error.message || "期望角度间隔无效");
    setText("captureRotationStatus", "样品旋转设置无效，无法开始采集。");
    return null;
  }
}

function lockRotationSettings() {
  const locked = Boolean(state.captureStarted);
  [
    "#multiViewEnabled",
    "#rotationIntervalDeg",
    "#rotationStartAngleDeg",
    "#rotationDirection",
    "#includeClosureView",
  ].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = locked;
  });
  document.querySelectorAll("[data-rotation-interval]").forEach((button) => {
    button.disabled = locked;
  });
}

function renderCaptureRotationStatus(plan = state.captureRotationPlan) {
  if (!plan || !plan.enabled) {
    setText("captureRotationStatus", "样品旋转：单视角；滤光片转轮独立切换多光谱波段。");
    return;
  }
  const total = plan.total_capture_views || plan.view_count || 0;
  const angles = (plan.angles_deg || []).map((angle) => `${formatAngleDeg(angle)}°`).join(" · ");
  const home = plan.returned_home ? "，采集结束已回 Home" : "，采集结束将回 Home";
  setText(
    "captureRotationStatus",
    `多角度采集：预计 ${total} 个视角，实际间隔 ${formatAngleDeg(plan.actual_interval_deg)}°，方向 ${plan.direction}${home}。角度：${angles}。滤光片转轮独立切换波段。`
  );
}

function collectRotationPayload() {
  const plan = renderRotationPlan();
  if (!plan) return null;
  return {
    enabled: Boolean(plan.enabled),
    expectedIntervalDeg: readRotationSettingsFromForm().expectedIntervalDeg,
    startAngleDeg: readRotationSettingsFromForm().startAngleDeg,
    direction: plan.direction,
    includeClosureView: Boolean(plan.include_closure_view),
  };
}

function readCameraSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(CAMERA_SETTINGS_KEY) || "{}");
    const resolution = parseResolutionSetting(saved.resolution || saved, DEFAULT_CAMERA_SETTINGS);
    return {
      deviceIndex: parseNumberSetting(saved.deviceIndex, DEFAULT_CAMERA_SETTINGS.deviceIndex),
      width: resolution.width,
      height: resolution.height,
      resolution: resolution.resolution,
      fps: parseNumberSetting(saved.fps, DEFAULT_CAMERA_SETTINGS.fps),
      fourcc: String(saved.fourcc || DEFAULT_CAMERA_SETTINGS.fourcc),
      autoExposureEnabled: legacyAutoEnabled(saved.autoExposureEnabled ?? saved.autoExposure, DEFAULT_CAMERA_SETTINGS.autoExposureEnabled),
      exposure: parseNumberSetting(saved.exposure, DEFAULT_CAMERA_SETTINGS.exposure),
      gainAuto: legacyAutoEnabled(saved.gainAuto, DEFAULT_CAMERA_SETTINGS.gainAuto),
      gain: parseNumberSetting(saved.gain, DEFAULT_CAMERA_SETTINGS.gain),
      autoWhiteBalanceEnabled: legacyAutoEnabled(saved.autoWhiteBalanceEnabled ?? saved.autoWhiteBalance, DEFAULT_CAMERA_SETTINGS.autoWhiteBalanceEnabled),
      whiteBalance: parseNumberSetting(saved.whiteBalance, DEFAULT_CAMERA_SETTINGS.whiteBalance),
      fx: parseNumberSetting(saved.fx, DEFAULT_CAMERA_SETTINGS.fx),
      fy: parseNumberSetting(saved.fy, DEFAULT_CAMERA_SETTINGS.fy),
      cx: parseNumberSetting(saved.cx, DEFAULT_CAMERA_SETTINGS.cx),
      cy: parseNumberSetting(saved.cy, DEFAULT_CAMERA_SETTINGS.cy),
    };
  } catch {
    return { ...DEFAULT_CAMERA_SETTINGS };
  }
}

function collectCameraSettingsFromForm() {
  const resolution = parseResolutionSetting($("#cameraResolution")?.value, DEFAULT_CAMERA_SETTINGS);
  const autoExposureEnabled = Boolean($("#cameraAutoExposureEnabled")?.checked);
  const gainAuto = Boolean($("#cameraGainAuto")?.checked);
  const autoWhiteBalanceEnabled = Boolean($("#cameraAutoWhiteBalanceEnabled")?.checked);
  return {
    deviceIndex: parseNumberSetting($("#cameraDeviceIndex")?.value, DEFAULT_CAMERA_SETTINGS.deviceIndex),
    width: resolution.width,
    height: resolution.height,
    resolution: resolution.resolution,
    fps: parseNumberSetting($("#cameraFps")?.value, DEFAULT_CAMERA_SETTINGS.fps),
    fourcc: $("#cameraFourcc")?.value.trim() || DEFAULT_CAMERA_SETTINGS.fourcc,
    autoExposureEnabled,
    autoExposure: autoExposureEnabled ? 0.75 : 0.25,
    exposure: autoExposureEnabled ? null : parseNumberSetting($("#cameraExposure")?.value, DEFAULT_CAMERA_SETTINGS.exposure),
    gainAuto,
    gain: gainAuto ? null : parseNumberSetting($("#cameraGain")?.value, DEFAULT_CAMERA_SETTINGS.gain),
    autoWhiteBalanceEnabled,
    autoWhiteBalance: autoWhiteBalanceEnabled ? 1 : 0,
    whiteBalance: autoWhiteBalanceEnabled ? null : parseNumberSetting($("#cameraWhiteBalance")?.value, DEFAULT_CAMERA_SETTINGS.whiteBalance),
    fx: parseNumberSetting($("#cameraFx")?.value, DEFAULT_CAMERA_SETTINGS.fx),
    fy: parseNumberSetting($("#cameraFy")?.value, DEFAULT_CAMERA_SETTINGS.fy),
    cx: parseNumberSetting($("#cameraCx")?.value, DEFAULT_CAMERA_SETTINGS.cx),
    cy: parseNumberSetting($("#cameraCy")?.value, DEFAULT_CAMERA_SETTINGS.cy),
  };
}

function applyCameraSettings(settings = readCameraSettings()) {
  if ($("#cameraDeviceIndex")) $("#cameraDeviceIndex").value = settings.deviceIndex;
  if ($("#cameraResolution")) $("#cameraResolution").value = `${settings.width}x${settings.height}`;
  if ($("#cameraFps")) $("#cameraFps").value = settings.fps;
  if ($("#cameraFourcc")) $("#cameraFourcc").value = settings.fourcc;
  if ($("#cameraExposure")) $("#cameraExposure").value = settings.exposure;
  if ($("#cameraAutoExposureEnabled")) $("#cameraAutoExposureEnabled").checked = Boolean(settings.autoExposureEnabled);
  if ($("#cameraGainAuto")) $("#cameraGainAuto").checked = Boolean(settings.gainAuto);
  if ($("#cameraGain")) $("#cameraGain").value = settings.gain ?? DEFAULT_CAMERA_SETTINGS.gain;
  if ($("#cameraAutoWhiteBalanceEnabled")) $("#cameraAutoWhiteBalanceEnabled").checked = Boolean(settings.autoWhiteBalanceEnabled);
  if ($("#cameraWhiteBalance")) $("#cameraWhiteBalance").value = settings.whiteBalance;
  if ($("#cameraFx")) $("#cameraFx").value = settings.fx;
  if ($("#cameraFy")) $("#cameraFy").value = settings.fy;
  if ($("#cameraCx")) $("#cameraCx").value = settings.cx;
  if ($("#cameraCy")) $("#cameraCy").value = settings.cy;
  updateCameraParameterControlState();
  setText("cameraSettingsStatus", "当前参数");
}

function saveCameraSettings() {
  const settings = collectCameraSettingsFromForm();
  localStorage.setItem(CAMERA_SETTINGS_KEY, JSON.stringify(settings));
  applyCameraSettings(settings);
  setText("cameraSettingsStatus", "默认配置已保存");
  addLog(`相机参数已保存：RGB index=${settings.deviceIndex}，${settings.resolution} @ ${settings.fps}fps ${settings.fourcc}；fx/fy=${settings.fx}/${settings.fy}。`);
}

function resetCameraSettings() {
  localStorage.removeItem(CAMERA_SETTINGS_KEY);
  applyCameraSettings({ ...DEFAULT_CAMERA_SETTINGS });
  setText("cameraSettingsStatus", "已恢复默认");
  addLog("相机参数已恢复为默认值。", "WARN");
}

function updateCameraParameterControlState() {
  const autoExposure = Boolean($("#cameraAutoExposureEnabled")?.checked);
  const gainAuto = Boolean($("#cameraGainAuto")?.checked);
  const autoWhiteBalance = Boolean($("#cameraAutoWhiteBalanceEnabled")?.checked);
  if ($("#cameraExposure")) $("#cameraExposure").disabled = autoExposure;
  if ($("#cameraGain")) $("#cameraGain").disabled = gainAuto;
  if ($("#cameraWhiteBalance")) $("#cameraWhiteBalance").disabled = autoWhiteBalance;
}

function setCameraSettingsTab(tab) {
  state.cameraSettingsTab = tab === "multispectral" ? "multispectral" : "rgb";
  $$(".camera-settings-tabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.cameraSettingsTab === state.cameraSettingsTab);
  });
  $$("[data-camera-settings-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.cameraSettingsPanel === state.cameraSettingsTab);
  });
  if (state.cameraSettingsTab !== "rgb" && state.rgbPreviewRunning) {
    stopRgbPreview();
  }
  if (state.cameraSettingsTab !== "multispectral" && state.multispectralPreviewRunning) {
    stopMultispectralPreview();
  }
  renderCameraSettingsStatus();
}

function formatCameraResolution(width, height, separator = " x ") {
  return width && height ? `${width}${separator}${height}` : "--";
}

function renderCameraSettingsStatus() {
  const rgb = state.cameraStatus?.rgb || {};
  const actual = rgb.actual || {};
  const requested = rgb.requested || {};
  const rgbDetected = Boolean(rgb.detected || rgb.available || rgb.connected);
  const rgbOpened = Boolean(rgb.opened || (rgb.connected && !rgb.detected));
  const rgbStreaming = Boolean(rgb.streaming || state.cameraStatus?.preview?.rgb?.running);
  const statusDot = $("#rgbCameraStatusDot");
  if (statusDot) statusDot.dataset.status = rgbStreaming || rgbOpened ? "connected" : rgbDetected ? "waiting" : rgb.error ? "error" : "idle";
  const rgbStatusText = rgbStreaming
    ? "预览中"
    : rgbDetected
      ? "已检测 / 预览已停止"
      : (rgb.error || "未检测到 RGB 相机");
  setText("rgbCameraStatusText", rgbStatusText);
  setText("rgbCameraTransportText", rgb.transport || "UVC / DirectShow");
  setText("rgbCameraActualResolutionText", formatCameraResolution(actual.width || rgb.resolution?.width || requested.width, actual.height || rgb.resolution?.height || requested.height));
  setText("rgbCameraActualFpsText", Number.isFinite(Number(actual.fps)) ? `${Number(actual.fps).toFixed(1).replace(".0", "")} FPS` : `${requested.fps || 25} FPS`);
  setText("rgbCameraActualFourccText", actual.fourcc || requested.fourcc || "MJPG");
  const capabilityText = {
    requested,
    actual,
    capabilities: rgb.capabilities || {},
    technicalError: rgb.technicalError || "",
  };
  setText("rgbCameraCapabilityText", JSON.stringify(capabilityText, null, 2));

  const multispectral = state.cameraStatus?.multispectral || {};
  const multiActual = multispectral.actual || {};
  const multiConnected = Boolean(multispectral.detected || multispectral.connected || multispectral.available);
  const multiStreaming = Boolean(multispectral.streaming || state.cameraStatus?.preview?.multispectral?.running);
  const multiDot = $("#multispectralCameraStatusDot");
  if (multiDot) multiDot.dataset.status = multiStreaming ? "connected" : multiConnected ? "waiting" : multispectral.error ? "error" : "idle";
  setText(
    "multispectralCameraStatusText",
    multiStreaming
      ? "预览中"
      : multiConnected
        ? (multispectral.available ? "已检测 / 预览已停止" : (multispectral.error || "已枚举 / 待打开"))
        : (multispectral.error || "等待相机供电 / 尚未完成真实连接验证")
  );
  setText("multispectralCameraTransportText", multispectral.transport || "GigE / RJ45");
  setText("multispectralCameraPixelFormatText", multiActual.pixelFormat || multispectral.pixelFormat || "DVP2 SDK");
  setText("multispectralCameraResolutionText", formatCameraResolution(multiActual.width || multispectral.resolution?.width, multiActual.height || multispectral.resolution?.height));
  setInputValueUnlessFocused("multispectralCameraIp", multiActual.cameraIp || "等待设备");
  setInputValueUnlessFocused("multispectralCameraMac", multiActual.cameraMac || "等待设备");
  setInputValueUnlessFocused("multispectralCameraSerial", multiActual.cameraSerial || multispectral.stableId || "等待设备");
  setInputValueUnlessFocused(
    "multispectralStreamFps",
    Number.isFinite(Number(multiActual.streamFps)) ? `${Number(multiActual.streamFps).toFixed(2)} FPS` : "等待设备"
  );
  setInputValueUnlessFocused("multispectralPixelFormat", multiActual.pixelFormat || multispectral.pixelFormat || "等待设备");
  setInputValueUnlessFocused("multispectralFrameDtype", multiActual.frameDtype || multispectral.frameDtype || "等待设备");
  setInputValueUnlessFocused("multispectralExposure", multiActual.exposure ?? multispectral.exposure ?? "等待设备");
  setInputValueUnlessFocused("multispectralGain", multiActual.gain ?? multispectral.gain ?? "等待设备");
  setInputValueUnlessFocused("multispectralTriggerMode", multispectral.capabilities?.triggerMode || "等待设备");
  setMultispectralNumericControl("multispectralExposureInput", multiActual.exposure ?? multispectral.exposure, multispectral.capabilities?.exposure);
  setMultispectralNumericControl("multispectralGainInput", multiActual.gain ?? multispectral.gain, multispectral.capabilities?.gain);
  const multiCapabilityText = {
    requested: multispectral.requested || {},
    actual: multiActual,
    capabilities: multispectral.capabilities || {},
    sdkPath: multispectral.sdkPath || "",
    dllPath: multispectral.dllPath || "",
    technicalError: multispectral.technicalError || "",
  };
  setText("multispectralCameraCapabilityText", JSON.stringify(multiCapabilityText, null, 2));
}

function setMultispectralNumericControl(id, value, capability = {}) {
  const input = document.getElementById(id);
  if (!input) return;
  if (Number.isFinite(Number(capability.min))) input.min = capability.min;
  if (Number.isFinite(Number(capability.max))) input.max = capability.max;
  if (Number.isFinite(Number(capability.step)) && Number(capability.step) > 0) input.step = capability.step;
  if (value !== null && value !== undefined && value !== "等待设备" && document.activeElement !== input) {
    input.value = value;
  }
}

function renderRgbApplySummary(result = null, error = null) {
  const node = $("#rgbApplySummary");
  if (!node) return;
  if (error) {
    node.dataset.status = "error";
    node.innerHTML = `<span>${escapeHtml(error.message || "参数应用失败")}</span>`;
    return;
  }
  if (!result) {
    node.dataset.status = "";
    node.innerHTML = "<span>请求值 / 实际值会在应用参数后显示。</span>";
    return;
  }
  const summary = result.summary || {};
  const restartText = result.restartRequired ? "已重新打开相机" : "动态参数已下发";
  const status = summary.matchesRequested ? "ok" : "warning";
  node.dataset.status = status;
  node.innerHTML = [
    `<span>${escapeHtml(restartText)}</span>`,
    `<span>分辨率：请求 ${escapeHtml(summary.requestedResolution || "--")} / 实际 ${escapeHtml(summary.actualResolution || "--")}</span>`,
    `<span>FPS：请求 ${escapeHtml(summary.requestedFps ?? "--")} / 实际 ${escapeHtml(summary.actualFps ?? "--")}</span>`,
    `<span>FOURCC：请求 ${escapeHtml(summary.requestedFourcc || "--")} / 实际 ${escapeHtml(summary.actualFourcc || "--")}${summary.matchesRequested ? "，已应用" : "，相机未完全接受请求参数"}</span>`,
    `<span>曝光：请求 ${escapeHtml(summary.requestedExposure ?? "Auto")} / 实际 ${escapeHtml(summary.actualExposure ?? "--")}</span>`,
    `<span>增益：请求 ${escapeHtml(summary.requestedGain ?? "默认")} / 实际 ${escapeHtml(summary.actualGain ?? "--")}</span>`,
    `<span>白平衡：请求 ${escapeHtml(summary.requestedWhiteBalance ?? "Auto")} / 实际 ${escapeHtml(summary.actualWhiteBalance ?? "--")}</span>`,
  ].join("");
}

function collectMultispectralCameraSettingsFromForm() {
  const payload = {};
  const exposure = $("#multispectralExposureInput")?.value;
  const gain = $("#multispectralGainInput")?.value;
  if (exposure !== undefined && exposure !== "") payload.exposure = Number(exposure);
  if (gain !== undefined && gain !== "") payload.gain = Number(gain);
  return payload;
}

function renderMultispectralApplySummary(result = null, error = null) {
  const node = $("#multispectralApplySummary");
  if (!node) return;
  if (error) {
    node.dataset.status = "error";
    node.innerHTML = `<span>${escapeHtml(error.message || "多光谱参数应用失败")}</span>`;
    return;
  }
  if (!result) {
    node.dataset.status = "";
    node.innerHTML = "<span>曝光 / 增益应用后会显示 DVP2 回读值。</span>";
    return;
  }
  const summary = result.summary || {};
  node.dataset.status = summary.matchesRequested ? "ok" : "warning";
  node.innerHTML = [
    "<span>DVP2 参数已下发并回读。</span>",
    `<span>曝光：请求 ${escapeHtml(summary.requestedExposure ?? "--")} μs / 实际 ${escapeHtml(summary.actualExposure ?? "--")} μs</span>`,
    `<span>增益：请求 ${escapeHtml(summary.requestedGain ?? "--")} / 实际 ${escapeHtml(summary.actualGain ?? "--")}</span>`,
    `<span>帧格式：${escapeHtml(summary.pixelFormat || "--")}，${escapeHtml(summary.frameDtype || "--")}</span>`,
  ].join("");
}

async function refreshCameraSettingsStatus() {
  try {
    const payload = await api("/api/camera/status");
    applyCameraStatus(payload.cameras || {});
    renderCameraSettingsStatus();
  } catch (error) {
    addLog(error.message || "相机状态读取失败。", "WARN");
  }
}

async function probeRgbCamera() {
  setText("cameraSettingsStatus", "正在重新检测 RGB 相机");
  try {
    const payload = await api("/api/camera/rgb/probe", {
      method: "POST",
      body: "{}",
    });
    const result = payload.result || {};
    applyCameraStatus({ ...(state.cameraStatus || {}), ...(result.status ? { rgb: result.status } : {}), preview: result.preview });
    renderCameraSettingsStatus();
    setText("cameraSettingsStatus", result.passed ? "RGB 相机已检测" : "RGB 相机检测失败");
    addLog(result.passed ? "RGB 相机重新检测完成，已通过 DirectShow 打开并取帧。" : "RGB 相机重新检测失败。", result.passed ? "INFO" : "WARN");
  } catch (error) {
    setText("cameraSettingsStatus", "RGB 相机检测失败");
    addLog(error.message || "RGB 相机重新检测失败。", "ERROR");
  }
}

async function applyRgbCameraSettings() {
  const settings = collectCameraSettingsFromForm();
  setText("cameraSettingsStatus", "正在应用到相机");
  renderRgbApplySummary(null);
  try {
    const payload = await api("/api/camera/rgb/apply-settings", {
      method: "POST",
      body: JSON.stringify(settings),
    });
    const result = payload.result || {};
    applyCameraStatus({ ...(state.cameraStatus || {}), rgb: result.status, preview: result.preview });
    renderCameraSettingsStatus();
    renderRgbApplySummary(result);
    setText("cameraSettingsStatus", "已回读实际参数");
    addLog(result.restartRequired ? "RGB 相机参数已应用，并重新打开相机。" : "RGB 动态参数已应用到相机。");
  } catch (error) {
    renderRgbApplySummary(null, error);
    setText("cameraSettingsStatus", "应用失败");
    addLog(error.message || "RGB 相机参数应用失败。", "ERROR");
  }
}

async function startRgbPreview() {
  try {
    const payload = await api("/api/camera/rgb/preview/start", {
      method: "POST",
      body: JSON.stringify({ width: 960, height: 540, fps: 12 }),
    });
    const result = payload.result || {};
    applyCameraStatus({ ...(state.cameraStatus || {}), ...(result.status ? { rgb: result.status } : {}), preview: result.preview });
    state.rgbPreviewRunning = true;
    renderCameraSettingsStatus();
    scheduleRgbPreviewFrames();
    setText("rgbLivePreviewEmpty", "正在读取 RGB 预览");
    addLog("RGB 实时预览已启动：960x540，最高 12 FPS。");
  } catch (error) {
    state.rgbPreviewRunning = false;
    setText("rgbLivePreviewEmpty", error.message || "RGB 预览启动失败");
    addLog(error.message || "RGB 预览启动失败。", "ERROR");
  }
}

async function stopRgbPreview() {
  clearRgbPreviewTimer();
  releaseRgbPreviewUrl();
  state.rgbPreviewRunning = false;
  const image = $("#rgbLivePreview");
  if (image) {
    image.classList.remove("active");
    image.removeAttribute("src");
  }
  setText("rgbLivePreviewEmpty", "RGB 预览未启动");
  try {
    const payload = await api("/api/camera/rgb/preview/stop", {
      method: "POST",
      body: "{}",
    });
    const result = payload.result || {};
    applyCameraStatus({ ...(state.cameraStatus || {}), ...(result.status ? { rgb: result.status } : {}), preview: result.preview });
    renderCameraSettingsStatus();
  } catch (error) {
    addLog(error.message || "停止 RGB 预览失败。", "WARN");
  }
}

function scheduleRgbPreviewFrames() {
  clearRgbPreviewTimer();
  fetchRgbPreviewFrame();
  state.rgbPreviewTimer = window.setInterval(fetchRgbPreviewFrame, 1000 / 12);
}

function clearRgbPreviewTimer() {
  if (state.rgbPreviewTimer) {
    window.clearInterval(state.rgbPreviewTimer);
    state.rgbPreviewTimer = null;
  }
}

function releaseRgbPreviewUrl() {
  if (state.rgbPreviewFrameUrl) {
    URL.revokeObjectURL(state.rgbPreviewFrameUrl);
    state.rgbPreviewFrameUrl = "";
  }
}

async function fetchRgbPreviewFrame() {
  if (!state.rgbPreviewRunning) return;
  if (state.rgbPreviewFetching) return;
  state.rgbPreviewFetching = true;
  try {
    const response = await fetch(`/api/camera/rgb/preview-frame?t=${Date.now()}`);
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        message = payload.error || message;
      } catch {}
      throw new Error(message);
    }
    const blob = await response.blob();
    releaseRgbPreviewUrl();
    state.rgbPreviewFrameUrl = URL.createObjectURL(blob);
    const image = $("#rgbLivePreview");
    if (image) {
      image.src = state.rgbPreviewFrameUrl;
      image.classList.add("active");
    }
    setText("rgbLivePreviewEmpty", "");
    setText("rgbPreviewMeta", `预览 ${response.headers.get("X-Preview-Width") || "960"} x ${response.headers.get("X-Preview-Height") || "540"}，源帧 ${response.headers.get("X-Source-Shape") || "未知"}；正式拍照配置仍保持 3840 x 2160。`);
  } catch (error) {
    const message = cameraFetchErrorMessage(error, "RGB 预览已停止");
    clearRgbPreviewTimer();
    state.rgbPreviewRunning = false;
    const image = $("#rgbLivePreview");
    if (image) image.classList.remove("active");
    setText("rgbLivePreviewEmpty", message);
    addLog(message, "WARN");
  } finally {
    state.rgbPreviewFetching = false;
  }
}

async function probeMultispectralCamera() {
  setText("cameraSettingsStatus", "正在重新检测多光谱相机");
  try {
    const payload = await api("/api/camera/multispectral/probe", {
      method: "POST",
      body: "{}",
    });
    const result = payload.result || {};
    applyCameraStatus({ ...(state.cameraStatus || {}), ...(result.status ? { multispectral: result.status } : {}), preview: result.preview });
    renderCameraSettingsStatus();
    setText("cameraSettingsStatus", result.passed ? "多光谱相机已检测" : "多光谱相机检测失败");
    addLog(result.passed ? "多光谱相机重新检测完成，已通过 DVP2 打开。" : "多光谱相机重新检测失败。", result.passed ? "INFO" : "WARN");
  } catch (error) {
    setText("cameraSettingsStatus", "多光谱相机检测失败");
    addLog(error.message || "多光谱相机重新检测失败。", "ERROR");
  }
}

async function applyMultispectralCameraSettings() {
  const settings = collectMultispectralCameraSettingsFromForm();
  setText("cameraSettingsStatus", "正在应用多光谱相机参数");
  renderMultispectralApplySummary(null);
  try {
    const payload = await api("/api/camera/multispectral/apply-settings", {
      method: "POST",
      body: JSON.stringify(settings),
    });
    const result = payload.result || {};
    applyCameraStatus({ ...(state.cameraStatus || {}), multispectral: result.status, preview: result.preview });
    renderCameraSettingsStatus();
    renderMultispectralApplySummary(result);
    setText("cameraSettingsStatus", "多光谱参数已回读");
    addLog("多光谱相机曝光 / 增益已通过 DVP2 应用并回读。");
  } catch (error) {
    renderMultispectralApplySummary(null, error);
    setText("cameraSettingsStatus", "多光谱参数应用失败");
    addLog(error.message || "多光谱相机参数应用失败。", "ERROR");
  }
}

async function startMultispectralPreview() {
  try {
    const payload = await api("/api/camera/multispectral/preview/start", {
      method: "POST",
      body: JSON.stringify({ width: 960, height: 540, fps: 8 }),
    });
    const result = payload.result || {};
    applyCameraStatus({ ...(state.cameraStatus || {}), ...(result.status ? { multispectral: result.status } : {}), preview: result.preview });
    state.multispectralPreviewRunning = true;
    renderCameraSettingsStatus();
    scheduleMultispectralPreviewFrames();
    setText("multispectralLivePreviewEmpty", "正在读取多光谱预览");
    addLog("多光谱实时预览已启动：960x540，最高 8 FPS。");
  } catch (error) {
    state.multispectralPreviewRunning = false;
    setText("multispectralLivePreviewEmpty", error.message || "多光谱预览启动失败");
    addLog(error.message || "多光谱预览启动失败。", "ERROR");
  }
}

async function stopMultispectralPreview() {
  clearMultispectralPreviewTimer();
  releaseMultispectralPreviewUrl();
  state.multispectralPreviewRunning = false;
  const image = $("#multispectralLivePreview");
  if (image) {
    image.classList.remove("active");
    image.removeAttribute("src");
  }
  setText("multispectralLivePreviewEmpty", "多光谱预览未启动");
  try {
    const payload = await api("/api/camera/multispectral/preview/stop", {
      method: "POST",
      body: "{}",
    });
    const result = payload.result || {};
    applyCameraStatus({ ...(state.cameraStatus || {}), ...(result.status ? { multispectral: result.status } : {}), preview: result.preview });
    renderCameraSettingsStatus();
  } catch (error) {
    addLog(error.message || "停止多光谱预览失败。", "WARN");
  }
}

function scheduleMultispectralPreviewFrames() {
  clearMultispectralPreviewTimer();
  fetchMultispectralPreviewFrame();
  state.multispectralPreviewTimer = window.setInterval(fetchMultispectralPreviewFrame, 1000 / 8);
}

function clearMultispectralPreviewTimer() {
  if (state.multispectralPreviewTimer) {
    window.clearInterval(state.multispectralPreviewTimer);
    state.multispectralPreviewTimer = null;
  }
}

function releaseMultispectralPreviewUrl() {
  if (state.multispectralPreviewFrameUrl) {
    URL.revokeObjectURL(state.multispectralPreviewFrameUrl);
    state.multispectralPreviewFrameUrl = "";
  }
}

async function fetchMultispectralPreviewFrame() {
  if (!state.multispectralPreviewRunning) return;
  if (state.multispectralPreviewFetching) return;
  state.multispectralPreviewFetching = true;
  try {
    const response = await fetch(`/api/camera/multispectral/preview-frame?t=${Date.now()}`);
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        message = payload.error || message;
      } catch {}
      throw new Error(message);
    }
    const blob = await response.blob();
    releaseMultispectralPreviewUrl();
    state.multispectralPreviewFrameUrl = URL.createObjectURL(blob);
    const image = $("#multispectralLivePreview");
    if (image) {
      image.src = state.multispectralPreviewFrameUrl;
      image.classList.add("active");
    }
    setText("multispectralLivePreviewEmpty", "");
    const frameMin = response.headers.get("X-Frame-Min");
    const frameMax = response.headers.get("X-Frame-Max");
    const frameMean = response.headers.get("X-Frame-Mean");
    const meanValue = Number(frameMean);
    const maxValue = Number(frameMax);
    const brightnessHint = Number.isFinite(meanValue) && Number.isFinite(maxValue) && meanValue < 2 && maxValue < 10
      ? " 画面亮度很低，请检查光源或曝光。"
      : "";
    setText(
      "multispectralPreviewMeta",
      `预览 ${response.headers.get("X-Preview-Width") || "960"} x ${response.headers.get("X-Preview-Height") || "540"}，源帧 ${response.headers.get("X-Source-Shape") || "未知"} ${response.headers.get("X-Source-Dtype") || ""} ${response.headers.get("X-Pixel-Format") || ""}；亮度 min=${frameMin || "--"} max=${frameMax || "--"} mean=${frameMean || "--"}。${brightnessHint}`
    );
  } catch (error) {
    const message = cameraFetchErrorMessage(error, "多光谱预览已停止");
    clearMultispectralPreviewTimer();
    state.multispectralPreviewRunning = false;
    const image = $("#multispectralLivePreview");
    if (image) image.classList.remove("active");
    setText("multispectralLivePreviewEmpty", message);
    addLog(message, "WARN");
  } finally {
    state.multispectralPreviewFetching = false;
  }
}

function hasActiveSample() {
  return Boolean(state.hasSample && state.sampleId);
}

function requireActiveSample(message = "请先创建当前样品。") {
  if (hasActiveSample()) return true;
  addLog(message, "WARN");
  setText("statusNote", message);
  switchView("capture", "sample");
  return false;
}

function isOfflineValidationReady() {
  return Boolean(
    state.devicePrep.connect
    && state.devicePrep.motor
    && state.devicePrep.light
  );
}

function isDevicePreparationReady() {
  return isOfflineValidationReady();
}

function hasDeviceFault() {
  const errorCode = Number(state.hardwareStatus?.errorCode);
  return Boolean(
    state.hardwareStatus?.emergencyStopped
    || (Number.isFinite(errorCode) && errorCode !== 0)
  );
}

function deriveSystemStatus() {
  if (hasDeviceFault()) {
    return {
      key: "device-error",
      label: "设备异常",
      tone: "error",
      detail: `故障码 ${state.hardwareStatus?.errorCode ?? "--"}，请先急停检查或清除故障。`,
    };
  }

  if (state.deviceCheckRunning || state.systemTask === "device-check") {
    return {
      key: "device-checking",
      label: "设备检查中",
      tone: "running",
      detail: "正在读取控制器和设备准备状态。",
    };
  }

  if (state.systemTask === "shape" || state.shapeJobId) {
    return {
      key: "shape-running",
      label: "形态分析中",
      tone: "running",
      detail: "正在执行本地形态与表面纹理分析。",
    };
  }

  if (state.systemTask === "ssc") {
    return {
      key: "ssc-running",
      label: "SSC 分析中",
      tone: "running",
      detail: "正在调用已选择的 SSC 模型。",
    };
  }

  if (state.systemTask === "acid") {
    return {
      key: "acid-running",
      label: "酸度分析中",
      tone: "running",
      detail: "正在调用已选择的 TA / pH 模型。",
    };
  }

  if (state.captureCompleting || (state.captureStarted && state.captureStep > 0 && state.captureStep < 4)) {
    return {
      key: "offline-capture",
      label: "离线验证中",
      tone: "running",
      detail: "当前流程由 create_offline_capture_dataset() 生成验证数据，不是真实相机采集。",
    };
  }

  if (state.grade || (Number.isFinite(state.ssc) && (Number.isFinite(state.ta) || Number.isFinite(state.ph)))) {
    return {
      key: "complete",
      label: "检测完成",
      tone: "complete",
      detail: "已有糖酸分析结果，可查看口感或报告区。",
    };
  }

  if (state.analysisDataDir && state.dataCheck.status !== "empty") {
    return {
      key: "waiting-analysis",
      label: "等待分析",
      tone: "ready",
      detail: "样品数据目录已载入，可以进行形态、SSC、TA 或 pH 分析。",
    };
  }

  if (hasActiveSample() && !state.currentCaptureValid) {
    return {
      key: "waiting-capture",
      label: "等待采集",
      tone: "ready",
      detail: "样品已创建，当前等待离线验证或未来真实采集。",
    };
  }

  if (hasActiveSample()) {
    return {
      key: "sample-created",
      label: "样品已创建",
      tone: "ready",
      detail: "当前样品会写入 metadata.json。",
    };
  }

  if (state.devicePrep.connect && !isDevicePreparationReady()) {
    return {
      key: "device-not-ready",
      label: "设备未就绪",
      tone: "warning",
      detail: "控制器或离线准备项尚未全部确认。",
    };
  }

  if (isDevicePreparationReady()) {
    return {
      key: "waiting-sample",
      label: "等待创建样品",
      tone: "ready",
      detail: "设备准备状态已满足当前离线流程，可以创建样品。",
    };
  }

  return {
    key: "waiting-device-check",
    label: "等待设备检查",
    tone: "waiting",
    detail: "请先完成设备准备；真实相机当前尚未接入。",
  };
}

function renderSystemStatus() {
  const status = deriveSystemStatus();
  const node = $("#systemStatus");
  if (!node) return status;
  node.textContent = `当前状态 · ${status.label}`;
  node.dataset.status = status.tone;
  node.title = status.detail || status.label;
  return status;
}

function requireDevicePreparation(message = "请先完成设备准备：连接检查、电机、光源、相机和标定检查。") {
  if (isDevicePreparationReady()) return true;
  addLog(message, "WARN");
  setText("statusNote", message);
  switchView("motor", "connect");
  return false;
}

function updateDevicePreparationControls() {
  const ready = isDevicePreparationReady();
  const hint = ready ? "离线验证可用；真实采集仍需完整相机与采集协调器。" : "请先完成设备检查：控制器、风扇、滤光轮和光源控制。";
  $("#startWorkflow") && ($("#startWorkflow").disabled = !ready);
  $("#startWorkflow") && ($("#startWorkflow").title = hint);
  $("#createSampleInline") && ($("#createSampleInline").disabled = !ready);
  $("#createSampleInline") && ($("#createSampleInline").title = hint);
  $("#createNewSample") && ($("#createNewSample").disabled = !ready);
  $("#createNewSample") && ($("#createNewSample").title = hint);
  $("#enterAnalysisFromCapture") && ($("#enterAnalysisFromCapture").disabled = !ready || !state.analysisDataDir);
  document.querySelectorAll("[data-step]").forEach((button) => {
    button.disabled = !ready;
    button.title = hint;
  });
  if (ready) setText("statusNote", "设备检查已完成：离线验证可用，真实采集仍需完整相机与采集协调器。");
}

function renderDevicePreparationStatus() {
  renderHardwareStatus();
  if (state.devicePrep.connect) {
    const serialText = state.hardwareStatus.connected
      ? `串口: ${state.hardwareStatus.port || "已连接"}`
      : "串口: 离线连接检查通过";
    setPill("serialStatus", serialText, "ok");
    setStepStatus("connect", "done");
  } else {
    setPill("serialStatus", "串口: 未连接", "");
    setStepStatus("connect", "waiting");
  }
  if (state.devicePrep.motor) {
    setPill("motorStatus", state.hardwareStatus.connected ? "电机: 硬件自检通过" : "电机: 离线自检通过", "ok");
    setStepStatus("motor", "done");
  }
  if (state.devicePrep.light) {
    setPill("lightStatus", "光源: 离线自检通过", "ok");
    setStepStatus("light", "done");
  }
  if (state.devicePrep.camera) {
    setPill("cameraStatus", "相机: 检查通过", "ok");
    setStepStatus("camera", "done");
  } else {
    const rgbReady = Boolean(state.cameraStatus.rgb?.connected || state.cameraStatus.rgb?.available);
    const spectralSdkMissing = state.cameraStatus.multispectral?.sdkAvailable === false;
    const cameraText = rgbReady
      ? "相机: RGB 可用，多光谱待接入"
      : spectralSdkMissing
        ? "相机: DVP2 SDK 未安装"
        : "相机: 未连接";
    setPill("cameraStatus", cameraText, "warn");
  }
  if (state.devicePrep.calibration) {
    state.calibrationStatus = "passed";
    renderCalibrationStatus();
  }
  updateDevicePreparationControls();
  renderSystemStatus();
}

function renderHardwareStatus() {
  const hardware = state.hardwareStatus || {};
  const connected = Boolean(hardware.connected);
  const port = hardware.port || "";
  const doorNames = {
    unknown: "未知",
    open: "已升起",
    closed: "已关闭",
    moving: "运动中",
    error: "异常",
  };
  setText("deviceFanState", `风扇: ${hardware.fanOn ? "开启" : connected ? "关闭" : "--"}`);
  setText("deviceDoorState", `门: ${doorNames[hardware.door] || hardware.door || "--"}`);
  setText("deviceWheelState", `滤光轮: ${hardware.wheelHomed ? `位置 ${hardware.wheelPosition}` : connected ? "未寻零" : "--"}`);
  setText("deviceRgbLedState", connected
    ? `RGB LED: 1路${hardware.rgbLed1On ? "开" : "关"} / 2路${hardware.rgbLed2On ? "开" : "关"}`
    : "RGB LED: --");
  setText("deviceTungstenState", connected
    ? `钨灯: 1路${hardware.tungsten1On ? "开" : "关"} / 2路${hardware.tungsten2On ? "开" : "关"}`
    : "钨灯: --");
  setText("deviceErrorState", `故障码: ${hardware.errorCode ?? "--"}`);
  setText("deviceConnectionHint", connected
    ? `已连接 ${port}。真实采集仍需等待完整采集协调器。`
    : "未连接 STM32 时仍可按离线调试流程验证软件界面。");
  setText("doorLiftState", `升降门: ${doorNames[hardware.door] || hardware.door || "未连接"}`);
  setText("filterWheelState", `滤光片轮: ${hardware.wheelHomed ? `位置 ${hardware.wheelPosition}` : connected ? "未寻零" : "待连接"}`);
  const sampleStage = connected
    ? "样品台: 未接入控制"
    : "样品台: 未连接";
  setText("sampleRotationState", sampleStage);
  $("#connectDevice") && ($("#connectDevice").disabled = connected);
  $("#disconnectDevice") && ($("#disconnectDevice").disabled = !connected);
  $("#faultClearDevice") && ($("#faultClearDevice").disabled = !connected);
  $("#refreshDeviceStatus") && ($("#refreshDeviceStatus").disabled = !connected);
  $("#hardwareSelfTest") && ($("#hardwareSelfTest").disabled = !connected);
  $("#hardwareMotionSelfTest") && ($("#hardwareMotionSelfTest").disabled = !connected);
}

function defaultDeviceChecks(status = "pending") {
  return Object.fromEntries(deviceCheckOrder.map((key) => ([
    key,
    {
      status,
      label: deviceCheckLabels[key],
      message: checkStatusText[status] || "待检查",
    },
  ])));
}

function checksFromHardwareStatus(device = state.hardwareStatus) {
  const connected = Boolean(device.connected);
  const cameras = device.cameras || state.cameraStatus || {};
  const checks = defaultDeviceChecks("pending");
  checks.controller = {
    status: connected ? "passed" : "not_connected",
    label: "控制器",
    message: connected ? `已连接 ${device.port || "STM32"}` : "STM32 尚未连接",
  };
  checks.door = {
    status: connected ? (device.door === "error" ? "failed" : ["open", "closed"].includes(device.door) ? "passed" : "warning") : "not_connected",
    label: "升降门",
    message: connected ? `门状态: ${device.door || "unknown"}` : "需要连接 STM32",
  };
  checks.fan = {
    status: connected ? (device.fanOn ? "passed" : "warning") : "not_connected",
    label: "风扇",
    message: connected ? (device.fanOn ? "风扇已开启" : "风扇未开启") : "需要连接 STM32",
  };
  checks.filterWheel = {
    status: connected ? (device.wheelHomed ? "passed" : "manual_required") : "not_connected",
    label: "滤光轮",
    message: connected ? (device.wheelHomed ? `位置 ${device.wheelPosition}` : "尚未寻零") : "需要连接 STM32",
  };
  checks.rgbCamera = cameraCheckFromStatus("rgb", cameras.rgb, "RGB 相机");
  checks.multispectralCamera = cameraCheckFromStatus("multispectral", cameras.multispectral, "多光谱相机");
  checks.light = {
    status: connected ? "manual_required" : "not_connected",
    label: "光源控制",
    message: connected ? "控制层可用，需人工确认输出" : "需要连接 STM32",
  };
  checks.calibration = {
    status: state.calibrationStatus === "passed" ? "passed" : "manual_required",
    label: "标定",
    message: state.calibrationStatus === "passed" ? "已人工确认" : "需要人工确认",
  };
  return checks;
}

function cameraCheckFromStatus(role, camera, label) {
  if (!camera) {
    return {
      status: "not_connected",
      label,
      message: role === "multispectral" ? "多光谱 GigE 相机 DVP2 SDK 尚未安装" : "RGB 相机未连接",
    };
  }
  if (role === "multispectral" && camera.sdkAvailable === false) {
    return {
      status: "sdk_missing",
      label,
      message: camera.error || "多光谱 GigE 相机 DVP2 SDK 尚未安装",
    };
  }
  const ready = Boolean(camera.connected || camera.available);
  const actual = camera.actual || {};
  const width = actual.width || camera.resolution?.width;
  const height = actual.height || camera.resolution?.height;
  const resolution = width && height
    ? `${width}x${height}`
    : "";
  const fps = Number.isFinite(Number(actual.fps)) ? `${Number(actual.fps).toFixed(1).replace(".0", "")}fps` : "";
  const fourcc = actual.fourcc || "";
  return {
    status: ready ? "passed" : "not_connected",
    label,
    message: ready ? ["已连接", resolution, fps, fourcc].filter(Boolean).join(" ") : (camera.error || `${label}未连接`),
  };
}

function renderDeviceChecks(checks = state.deviceChecks, detail = state.deviceCheckDetail) {
  const grid = $("#deviceCheckGrid");
  const normalized = Object.keys(checks || {}).length ? checks : defaultDeviceChecks();
  state.deviceChecks = normalized;
  if (grid) {
    grid.innerHTML = deviceCheckOrder.map((key) => {
      const item = normalized[key] || { status: "pending", label: deviceCheckLabels[key], message: "待检查" };
      const status = item.status || "pending";
      return `<div class="device-check-item" data-status="${escapeHtml(status)}"><span>${escapeHtml(item.label || deviceCheckLabels[key])}</span><small>${escapeHtml(checkStatusText[status] || status)} · ${escapeHtml(item.message || "")}</small></div>`;
    }).join("");
  }

  const values = Object.values(normalized);
  const blocked = values.filter((item) => ["failed", "not_connected", "sdk_missing"].includes(item.status || "")).length;
  const manual = values.filter((item) => item.status === "manual_required").length;
  const controllerOk = normalized.controller?.status === "passed";
  const offlineReady = controllerOk && normalized.fan?.status === "passed" && ["passed", "manual_required"].includes(normalized.filterWheel?.status);
  const summary = state.deviceCheckRunning
    ? "设备检查中，包含滤光轮归零/运动检查..."
    : offlineReady
      ? "离线验证可用；真实采集不可用，等待完整相机服务和采集协调器。"
      : controllerOk
        ? `设备未完全就绪；${manual} 项需要确认，${blocked} 项不可用。`
        : "未连接 STM32；请先选择串口并连接。";
  setText("deviceCheckSummary", summary);
  const text = detail ? JSON.stringify(detail, null, 2) : "暂无设备详情";
  setText("deviceDetailText", text);
}

function applyHardwareStatus(device = {}) {
  state.hardwareStatus = { ...state.hardwareStatus, ...(device || {}) };
  applyCameraStatus(device.cameras || state.cameraStatus);
  if (state.hardwareStatus.connected) {
    state.devicePrep.connect = true;
  }
  renderHardwareStatus();
  renderDeviceChecks(checksFromHardwareStatus());
  renderSystemStatus();
}

function applyCameraStatus(cameras = {}) {
  state.cameraStatus = {
    ...state.cameraStatus,
    ...(cameras || {}),
  };
  renderCameraSettingsStatus();
}

function renderSerialPorts() {
  const select = $("#serialPort");
  if (!select) return;
  const selected = select.value;
  const ports = state.serialPorts || [];
  select.innerHTML = "";
  if (!ports.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "无可用串口";
    select.appendChild(option);
    return;
  }
  ports.forEach((port) => {
    const option = document.createElement("option");
    option.value = port.device || "";
    option.textContent = [port.device, port.description].filter(Boolean).join(" · ");
    select.appendChild(option);
  });
  if (ports.some((port) => port.device === selected)) select.value = selected;
}

async function loadDevicePorts() {
  try {
    const payload = await api("/api/device/ports");
    state.serialPorts = payload.ports || [];
    renderSerialPorts();
    addLog(state.serialPorts.length ? `已读取 ${state.serialPorts.length} 个串口。` : "未发现可用串口。", state.serialPorts.length ? "INFO" : "WARN");
  } catch (error) {
    state.serialPorts = [];
    renderSerialPorts();
    addLog(error.message || "串口列表读取失败。", "WARN");
  }
}

async function refreshHardwareStatus() {
  try {
    const payload = await api("/api/device/status");
    applyHardwareStatus(payload.device || {});
    renderDevicePreparationStatus();
  } catch (error) {
    addLog(error.message || "硬件状态读取失败。", "WARN");
  }
}

async function connectDevice() {
  const port = $("#serialPort")?.value || "";
  if (!port) {
    addLog("请选择串口后再连接 STM32。", "WARN");
    return;
  }
  try {
    const payload = await api("/api/device/connect", {
      method: "POST",
      body: JSON.stringify({ port }),
    });
    applyHardwareStatus(payload.device || {});
    state.devicePrep.connect = true;
    setText("statusNote", "STM32 已连接，串口通信检查通过。");
    addLog(`STM32 已连接并通过 PING：${state.hardwareStatus.port || port}`);
    await syncDevicePreparation();
  } catch (error) {
    setText("statusNote", error.message || "STM32 连接失败。");
    addLog(error.message || "STM32 连接失败。", "ERROR");
  }
}

async function disconnectDevice() {
  try {
    const payload = await api("/api/device/disconnect", {
      method: "POST",
      body: "{}",
    });
    applyHardwareStatus(payload.device || { connected: false });
    state.devicePrep.connect = false;
    addLog("已断开 STM32，并在断开前尝试执行安全停止。", "WARN");
    await syncDevicePreparation();
  } catch (error) {
    addLog(error.message || "断开 STM32 失败。", "ERROR");
  }
}

async function runHardwareSelfTest(includeMotion = false) {
  try {
    const payload = await api("/api/device/self-test", {
      method: "POST",
      body: JSON.stringify({ includeMotion }),
    });
    applyHardwareStatus(payload.result?.status || {});
    if (payload.result?.checks) {
      state.deviceCheckDetail = {
        includeMotion,
        device: payload.result.status || state.hardwareStatus,
        checks: payload.result.checks,
      };
      renderDeviceChecks({ ...checksFromHardwareStatus(payload.result.status || state.hardwareStatus), ...payload.result.checks }, state.deviceCheckDetail);
    }
    state.devicePrep.connect = true;
    if (includeMotion) state.devicePrep.motor = true;
    setStepStatus(includeMotion ? "motor" : "connect", "done");
    setText("statusNote", includeMotion ? "滤光片轮寻零自检通过。" : "STM32 通信自检通过。");
    addLog(includeMotion ? "硬件自检通过：PING、风扇开启、滤光片轮寻零已执行。" : "硬件通信自检通过：PING 与风扇开启已执行。");
    await syncDevicePreparation();
  } catch (error) {
    addLog(error.message || "硬件自检失败。", "ERROR");
  }
}

async function runUnifiedDeviceCheck() {
  state.deviceCheckRunning = true;
  state.systemTask = "device-check";
  renderDeviceChecks(defaultDeviceChecks("checking"));
  renderSystemStatus();
  const startButton = $("#startDeviceCheck");
  if (startButton) startButton.disabled = true;

  try {
    const statusPayload = await api("/api/device/status");
    const device = statusPayload.device || {};
    applyHardwareStatus(device);

    if (!device.connected) {
      state.devicePrep = {
        ...state.devicePrep,
        connect: false,
        motor: false,
        light: false,
        camera: false,
      };
      state.deviceCheckDetail = { device };
      renderDeviceChecks(checksFromHardwareStatus(device), state.deviceCheckDetail);
      setText("statusNote", "STM32 尚未连接：离线界面可查看，样品采集前需要先完成设备检查。");
      await syncDevicePreparation();
      return;
    }

    const selfTestPayload = await api("/api/device/self-test", {
      method: "POST",
      body: JSON.stringify({ includeMotion: true }),
    });
    const result = selfTestPayload.result || {};
    const checkedDevice = result.status || device;
    applyHardwareStatus(checkedDevice);
    const checks = { ...checksFromHardwareStatus(checkedDevice), ...(result.checks || {}) };
    state.deviceCheckDetail = {
      includeMotion: true,
      device: checkedDevice,
      checks,
      trueCaptureReady: false,
      offlineValidationReady: true,
    };
    renderDeviceChecks(checks, state.deviceCheckDetail);

    state.devicePrep.connect = checks.controller?.status === "passed";
    state.devicePrep.motor = ["passed", "manual_required"].includes(checks.filterWheel?.status);
    state.devicePrep.light = ["passed", "manual_required"].includes(checks.light?.status);
    state.devicePrep.camera = false;
    state.devicePrep.calibration = state.calibrationStatus === "passed";
    setStepStatus("connect", state.devicePrep.connect ? "done" : "warning");
    setStepStatus("motor", state.devicePrep.motor ? "done" : "warning");
    setStepStatus("light", state.devicePrep.light ? "done" : "warning");
    setStepStatus("camera", "warning");
    setText("statusNote", "设备检查完成：离线验证可用；RGB 走 OpenCV/DirectShow 检查，多光谱等待 DVP2 SDK，真实采集不可用。");
    addLog("一键设备检查完成：已复用 STM32 状态读取与硬件自检；相机仍为尚未接入。");
    await syncDevicePreparation();
  } catch (error) {
    const checks = checksFromHardwareStatus(state.hardwareStatus);
    checks.controller = {
      status: state.hardwareStatus.connected ? "failed" : "not_connected",
      label: "控制器",
      message: error.message || "设备检查失败",
    };
    state.deviceCheckDetail = {
      error: error.message || "设备检查失败",
      device: state.hardwareStatus,
    };
    renderDeviceChecks(checks, state.deviceCheckDetail);
    setText("statusNote", error.message || "设备检查失败。");
    addLog(error.message || "设备检查失败。", "ERROR");
  } finally {
    state.deviceCheckRunning = false;
    if (state.systemTask === "device-check") state.systemTask = "";
    if (startButton) startButton.disabled = false;
    renderSystemStatus();
  }
}

async function faultClearDevice() {
  try {
    const payload = await api("/api/device/fault-clear", {
      method: "POST",
      body: "{}",
    });
    applyHardwareStatus(payload.device || {});
    addLog("STM32 故障状态已请求清除。");
  } catch (error) {
    addLog(error.message || "清除故障失败。", "ERROR");
  }
}

async function emergencyStopDevice() {
  if (state.hardwareStatus.connected) {
    try {
      const payload = await api("/api/device/emergency-stop", {
        method: "POST",
        body: "{}",
      });
      applyHardwareStatus(payload.device || {});
      setPill("motorStatus", "电机: 已安全停止", "warn");
      setPill("lightStatus", "光源: 已关闭", "warn");
      addLog("紧急停止已发送到 STM32。", "WARN");
      return;
    } catch (error) {
      addLog(error.message || "发送急停失败，已进入本地停止状态。", "ERROR");
    }
  }
  setPill("motorStatus", "电机: 已停止", "warn");
  setPill("lightStatus", "光源: 已关闭", "warn");
  addLog("紧急停止已触发：当前未连接 STM32，已按本地离线状态处理。", "WARN");
}

async function syncDevicePreparation() {
  renderDevicePreparationStatus();
  try {
    await api("/api/device-preparation", {
      method: "POST",
      body: JSON.stringify(state.devicePrep),
    });
  } catch (error) {
    addLog(error.message || "设备准备状态同步失败。", "WARN");
  }
}

function renderCurrentSample() {
  setText("currentSampleName", state.sampleName || "未创建样品");
  setText("currentSampleId", state.sampleId ? `${state.sampleId} · ${state.fruitType || "--"} / ${state.variety || "generic"}` : "请先创建当前样品");
  setText("resultSampleName", state.sampleName || "--");
  setText("sampleCreateStatus", hasActiveSample() ? "已创建" : "等待创建");
  $("#sampleCreateStatus")?.classList.toggle("ready", hasActiveSample());
  if ($("#sampleId")) $("#sampleId").value = state.sampleId || "";
  if ($("#saveRootDir")) {
    $("#saveRootDir").value = state.saveRootDir || "";
    $("#saveRootDir").title = state.saveRootDir || "";
  }
  if ($("#sampleFolderPath")) {
    $("#sampleFolderPath").value = state.currentCaptureDir || "";
    $("#sampleFolderPath").title = state.currentCaptureDir || "";
  }
  const disabled = !hasActiveSample();
  if ($("#selectDataset")) $("#selectDataset").disabled = false;
  updateShapeRunButtonState();
  if ($("#enterAnalysisFromCapture")) $("#enterAnalysisFromCapture").disabled = disabled || !state.analysisDataDir;
  if ($("#openCaptureFolder")) $("#openCaptureFolder").disabled = disabled || !state.currentCaptureDir;
  if ($("#chooseSaveRoot")) $("#chooseSaveRoot").disabled = state.captureStarted;
  lockRotationSettings();
  updateDevicePreparationControls();
  updateAnalysisButtonStates();
  updateShapeMode();
  renderSystemStatus();
}

function updateAnalysisButtonStates() {
  const sscAvailable = hasActiveSample() && Boolean(state.selectedSscModelId);
  const taAvailable = hasActiveSample() && Boolean(state.selectedTaModelId || state.selectedPhModelId);
  const sscButton = $("#startSscAnalysis");
  const acidButton = $("#startAcidAnalysis");
  if (sscButton) sscButton.disabled = !sscAvailable;
  if (acidButton) acidButton.disabled = !taAvailable;
  if (hasActiveSample() && !state.selectedSscModelId) setText("sscModelStatus", "无兼容模型");
  if (hasActiveSample() && !state.selectedTaModelId && !state.selectedPhModelId) setText("acidModelStatus", "无兼容模型");
  renderModelOverview();
}

function clearSampleDependentState() {
  state.ssc = null;
  state.ta = null;
  state.ph = null;
  state.ratio = null;
  state.grade = null;
  state.captureStep = 0;
  state.captureStarted = false;
  state.shapeJobId = null;
  state.shapeStartedAt = null;
  state.currentCaptureDir = "";
  state.currentCaptureValid = false;
  state.analysisDataDir = "";
  state.saveRootDir = "";
  state.captureRotationPlan = null;
  state.imageBrowser.images = [];
  state.imageBrowser.index = 0;
  state.dataCheck = { status: "empty", rgbCount: 0, spectralCount: 0, pairCount: 0 };
  state.sampleSession.rgbFiles = [];
  state.sampleSession.multispectralFiles = [];
  state.sampleSession.analysisDataDir = "";
  state.sampleSession.sscResult = null;
  state.sampleSession.taResult = null;
  state.sampleSession.phResult = null;
  setText("resultSsc", "--");
  setText("resultTa", "--");
  setText("resultPh", "--");
  setText("tasteRatio", "--");
  setText("tasteGrade", "--");
  setText("tasteExplain", "等待糖度与酸度数据。");
  renderSscResult({});
  renderAcidResult({}, {});
  renderDatasetImage();
  renderDataCheck({ status: "empty", rgbCount: 0, spectralCount: 0, pairCount: 0, message: "请先创建当前样品。" });
  resetShapeStatus();
  renderRotationPlan();
  updateCurrentCaptureControls();
  resetCaptureStepStatuses();
  renderCalibrationStatus();
  renderSystemStatus();
}

function addLog(message, level = "INFO") {
  const log = $("#runLog");
  if (!log) return;
  const stamp = new Date().toTimeString().slice(0, 8);
  log.textContent += `\n[${stamp}] [${level}] ${message}`;
  log.scrollTop = log.scrollHeight;
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
  } catch (error) {
    throw cameraFetchError(error, "无法连接本地后端，请确认软件后端仍在运行。");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    const error = new Error(payload.error || payload.message || `HTTP ${response.status}`);
    error.payload = payload;
    throw error;
  }
  return payload;
}

function cameraFetchError(error, fallback) {
  const message = error?.message === "Failed to fetch" ? fallback : (error?.message || fallback);
  const wrapped = new Error(message);
  wrapped.originalError = error;
  return wrapped;
}

function cameraFetchErrorMessage(error, fallback) {
  return error?.message === "Failed to fetch"
    ? "无法连接本地后端，请确认软件后端仍在运行。"
    : (error?.message || fallback);
}

async function selectFolderPath({ purpose = "folder", initial = "" } = {}) {
  const query = new URLSearchParams({ purpose, initial });
  return api(`/api/select-folder?${query.toString()}`);
}

function setPathDisplay(selector, value = "") {
  const node = $(selector);
  if (!node) return;
  node.value = value || "";
  node.title = value || "";
}

function directoryNameForInput(value = "", fallback = "") {
  const text = String(value || "").trim();
  if (!text) return fallback;
  if (!/[\\/]/.test(text)) return text;
  const parts = text.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.at(-1) || fallback;
}

function validateImageDirName(name, label) {
  const value = String(name || "").trim();
  if (!value) return `${label}不能为空。`;
  if (value === "." || value === "..") return `${label}不能是 . 或 ..。`;
  if (/[\\/:*?"<>|]/.test(value)) return `${label}不能包含路径分隔符或 Windows 非法字符。`;
  return "";
}

function applyImageDirNames({ rgbDirName = "rgb", multispectralDirName = "multispectral", otherImageDirs = state.otherImageDirs } = {}) {
  state.rgbDirName = rgbDirName || "rgb";
  state.multispectralDirName = multispectralDirName || "multispectral";
  state.otherImageDirs = Array.isArray(otherImageDirs) ? otherImageDirs : [];
  if ($("#colorDir")) $("#colorDir").value = state.rgbDirName;
  if ($("#depthDir")) $("#depthDir").value = state.multispectralDirName;
}

function imageDirSettingsDefaults() {
  return {
    rgbDirName: state.rgbDirName || $("#colorDir")?.value || "rgb",
    multispectralDirName: state.multispectralDirName || $("#depthDir")?.value || "multispectral",
  };
}

let imageDirSettingsResolver = null;
function closeImageDirSettingsModal(result = null) {
  const modal = $("#imageDirSettingsModal");
  if (modal) modal.hidden = true;
  const resolver = imageDirSettingsResolver;
  imageDirSettingsResolver = null;
  if (resolver) resolver(result);
}

function openImageDirSettingsModal(defaults = imageDirSettingsDefaults()) {
  const modal = $("#imageDirSettingsModal");
  if (!modal) return Promise.resolve(defaults);
  $("#captureRgbDirName").value = defaults.rgbDirName || "rgb";
  $("#captureMultispectralDirName").value = defaults.multispectralDirName || "multispectral";
  setText("imageDirSettingsHint", "目录名称只保存文件夹名，不能包含路径分隔符或 Windows 非法字符。");
  modal.hidden = false;
  $("#captureRgbDirName")?.focus();
  return new Promise((resolve) => {
    imageDirSettingsResolver = resolve;
  });
}

function confirmImageDirSettingsModal() {
  const rgbDirName = $("#captureRgbDirName")?.value.trim() || "";
  const multispectralDirName = $("#captureMultispectralDirName")?.value.trim() || "";
  const error = validateImageDirName(rgbDirName, "RGB 图像文件夹名称")
    || validateImageDirName(multispectralDirName, "多光谱图像文件夹名称")
    || (rgbDirName.toLowerCase() === multispectralDirName.toLowerCase() ? "RGB 和多光谱目录名称不能相同。" : "");
  if (error) {
    setText("imageDirSettingsHint", error);
    return;
  }
  closeImageDirSettingsModal({ rgbDirName, multispectralDirName });
}

function selectSuggestedDir(directories, role) {
  return (directories || []).find((item) => item.suggestedRole === role)?.name || "";
}

function fillFolderSelect(selector, directories, selected = "") {
  const select = $(selector);
  if (!select) return;
  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "请选择目录";
  select.appendChild(placeholder);
  (directories || []).forEach((item) => {
    const option = document.createElement("option");
    option.value = item.name;
    option.textContent = `${item.name} · ${Number(item.imageCount || 0)} 张`;
    select.appendChild(option);
  });
  select.value = selected || "";
}

function renderOtherFolderOptions(directories, selected = []) {
  const container = $("#folderSelectOtherDirs");
  if (!container) return;
  container.innerHTML = "";
  const selectedSet = new Set(selected);
  (directories || []).forEach((item) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    const text = document.createElement("span");
    input.type = "checkbox";
    input.value = item.name;
    input.checked = selectedSet.has(item.name);
    text.textContent = item.name;
    text.title = item.name;
    label.appendChild(input);
    label.appendChild(text);
    container.appendChild(label);
  });
}

let imageFolderSelectResolver = null;
function closeImageFolderSelectModal(result = null) {
  const modal = $("#imageFolderSelectModal");
  if (modal) modal.hidden = true;
  const resolver = imageFolderSelectResolver;
  imageFolderSelectResolver = null;
  if (resolver) resolver(result);
}

function openImageFolderSelectModal(inspectResult = {}) {
  const modal = $("#imageFolderSelectModal");
  if (!modal) return Promise.resolve(null);
  const directories = inspectResult.directories || [];
  const suggestedRgb = selectSuggestedDir(directories, "rgb");
  const suggestedSpectral = selectSuggestedDir(directories, "multispectral");
  const otherDefaults = directories
    .filter((item) => item.suggestedRole === "other")
    .map((item) => item.name);
  setPathDisplay("#folderSelectParentDir", inspectResult.parentDir || "");
  fillFolderSelect("#folderSelectRgbDir", directories, suggestedRgb);
  fillFolderSelect("#folderSelectMultispectralDir", directories, suggestedSpectral);
  renderOtherFolderOptions(directories, otherDefaults);
  setText("imageFolderSelectHint", suggestedRgb && suggestedSpectral ? "已根据目录名给出默认建议，确认前可手动调整。" : "未完整匹配到 RGB 或多光谱目录，请手动选择。");
  modal.hidden = false;
  $("#folderSelectRgbDir")?.focus();
  return new Promise((resolve) => {
    imageFolderSelectResolver = resolve;
  });
}

function confirmImageFolderSelectModal() {
  const rgbDirName = $("#folderSelectRgbDir")?.value || "";
  const multispectralDirName = $("#folderSelectMultispectralDir")?.value || "";
  if (!rgbDirName || !multispectralDirName) {
    setText("imageFolderSelectHint", "RGB 图像目录和多光谱图像目录都必须选择。");
    return;
  }
  if (rgbDirName === multispectralDirName) {
    setText("imageFolderSelectHint", "RGB 图像目录和多光谱图像目录不能相同。");
    return;
  }
  const otherImageDirs = Array.from(document.querySelectorAll("#folderSelectOtherDirs input:checked"))
    .map((input) => input.value)
    .filter((name) => name && name !== rgbDirName && name !== multispectralDirName);
  closeImageFolderSelectModal({ rgbDirName, multispectralDirName, otherImageDirs });
}

async function inspectImageFolders(parentDir) {
  const query = new URLSearchParams({ parentDir });
  return api(`/api/inspect-image-folders?${query.toString()}`);
}

function updateShapeRunButtonState() {
  const runButton = $("#runShapeAnalysis");
  if (!runButton || state.shapeJobId) return;
  const isPointcloud = ($("#shapeMode")?.value || state.shapeMode || "morphology2d") === "pointcloud3d";
  const hasDataset = Boolean(state.analysisDataDir || $("#datasetDir")?.value);
  runButton.textContent = isPointcloud ? "点云建模待接入" : "开始形态分析";
  runButton.disabled = Boolean(isPointcloud || !hasDataset);
  runButton.title = isPointcloud
    ? "当前硬件未提供深度信息，三维建模入口暂未接入。"
    : !hasDataset
        ? "请先选择并检查样品文件夹。"
        : "";
}

function initPointcloudViewer() {
  const canvas = $("#pointcloudCanvas");
  if (!canvas) return;
  const gl = canvas.getContext("webgl", { antialias: true, preserveDrawingBuffer: true });
  if (!gl) {
    setText("pointcloudHint", "当前浏览器不支持 WebGL，显示静态预览图");
    return;
  }

  const vertexSource = `
    attribute vec3 aPosition;
    attribute vec3 aColor;
    uniform mat4 uMvp;
    varying vec3 vColor;
    void main() {
      gl_Position = uMvp * vec4(aPosition, 1.0);
      gl_PointSize = 3.0;
      vColor = aColor;
    }
  `;
  const fragmentSource = `
    precision mediump float;
    varying vec3 vColor;
    void main() {
      vec2 p = gl_PointCoord - vec2(0.5);
      if (dot(p, p) > 0.25) discard;
      gl_FragColor = vec4(vColor, 1.0);
    }
  `;
  const program = createPointcloudProgram(gl, vertexSource, fragmentSource);
  if (!program) return;

  state.viewer = {
    canvas,
    gl,
    program,
    positionBuffer: gl.createBuffer(),
    colorBuffer: gl.createBuffer(),
    count: 0,
    rotationX: -0.62,
    rotationY: 0.78,
    zoom: 3.2,
    dragging: false,
    lastX: 0,
    lastY: 0,
    frame: 0,
  };

  gl.enable(gl.DEPTH_TEST);
  gl.clearColor(1, 1, 1, 1);

  canvas.addEventListener("pointerdown", (event) => {
    if (!state.viewer?.count) return;
    state.viewer.dragging = true;
    state.viewer.lastX = event.clientX;
    state.viewer.lastY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    const viewer = state.viewer;
    if (!viewer?.dragging) return;
    const dx = event.clientX - viewer.lastX;
    const dy = event.clientY - viewer.lastY;
    viewer.rotationY += dx * 0.008;
    viewer.rotationX += dy * 0.008;
    viewer.rotationX = Math.max(-1.45, Math.min(1.45, viewer.rotationX));
    viewer.lastX = event.clientX;
    viewer.lastY = event.clientY;
    schedulePointcloudDraw();
  });
  canvas.addEventListener("pointerup", (event) => {
    if (!state.viewer) return;
    state.viewer.dragging = false;
    try {
      canvas.releasePointerCapture(event.pointerId);
    } catch (_) {
      // Pointer capture can already be released by the browser.
    }
  });
  canvas.addEventListener("wheel", (event) => {
    const viewer = state.viewer;
    if (!viewer?.count) return;
    event.preventDefault();
    viewer.zoom = Math.max(1.6, Math.min(7.5, viewer.zoom + event.deltaY * 0.004));
    schedulePointcloudDraw();
  }, { passive: false });

  $("#resetPointcloudView")?.addEventListener("click", () => resetPointcloudView(true));
  window.addEventListener("resize", resizePointcloudCanvas);
  if (window.ResizeObserver) {
    new ResizeObserver(resizePointcloudCanvas).observe(canvas);
  }
  resizePointcloudCanvas();
}

function createPointcloudProgram(gl, vertexSource, fragmentSource) {
  const vertex = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
  if (!vertex || !fragment) return null;
  const program = gl.createProgram();
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    addLog(`WebGL 程序链接失败: ${gl.getProgramInfoLog(program)}`, "ERROR");
    return null;
  }
  return program;
}

function compileShader(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    addLog(`WebGL 着色器编译失败: ${gl.getShaderInfoLog(shader)}`, "ERROR");
    return null;
  }
  return shader;
}

async function loadPointcloudViewer(plyUrl) {
  const viewer = state.viewer;
  if (!viewer || !plyUrl) return false;
  try {
    setText("pointcloudHint", "正在加载标准散点点云...");
    const response = await fetch(`${plyUrl}?t=${Date.now()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const cloud = parseAsciiPly(await response.text());
    if (!cloud.count) throw new Error("PLY 中没有有效顶点");
    uploadPointcloud(cloud);
    $(".pointcloud-box")?.classList.add("viewer-ready");
    setText("pointcloudHint", `点云 ${cloud.count} 点，拖拽旋转，滚轮缩放，当前为标准观察比例`);
    addLog("标准散点点云已加载。");
    return true;
  } catch (error) {
    $(".pointcloud-box")?.classList.remove("viewer-ready");
    setText("pointcloudHint", "点云模型读取失败");
    addLog(`点云模型读取失败: ${error.message}`, "WARN");
    return false;
  }
}

function parseAsciiPly(text) {
  const lines = text.split(/\r?\n/);
  let headerEnd = -1;
  let vertexCount = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (line.startsWith("element vertex")) {
      vertexCount = Number(line.split(/\s+/)[2] || 0);
    }
    if (line === "end_header") {
      headerEnd = index;
      break;
    }
  }
  if (headerEnd < 0 || !vertexCount) return { count: 0 };

  const rawPositions = [];
  const rawColors = [];
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (let index = headerEnd + 1; index < lines.length && rawPositions.length / 3 < vertexCount; index += 1) {
    const parts = lines[index].trim().split(/\s+/).map(Number);
    if (parts.length < 3 || parts.slice(0, 3).some((item) => !Number.isFinite(item))) continue;
    const x = parts[0];
    const y = parts[1];
    const z = parts[2];
    rawPositions.push(x, y, z);
    min[0] = Math.min(min[0], x);
    min[1] = Math.min(min[1], y);
    min[2] = Math.min(min[2], z);
    max[0] = Math.max(max[0], x);
    max[1] = Math.max(max[1], y);
    max[2] = Math.max(max[2], z);
    rawColors.push(0, 0, 0);
  }

  const count = rawPositions.length / 3;
  if (!count) return { count: 0 };
  const center = [
    (min[0] + max[0]) / 2,
    (min[1] + max[1]) / 2,
    (min[2] + max[2]) / 2,
  ];
  const ranges = [
    Math.max(max[0] - min[0], 1),
    Math.max(max[1] - min[1], 1),
    Math.max(max[2] - min[2], 1),
  ];
  const scales = ranges.map((value) => value / 2);
  const zLow = min[2];
  const zHigh = max[2];
  const zRange = Math.max(zHigh - zLow, 1);
  const positions = new Float32Array(rawPositions.length);
  const colors = new Float32Array(rawColors.length);
  for (let index = 0; index < count; index += 1) {
    const z = rawPositions[index * 3 + 2];
    const depthMix = Math.max(0, Math.min(1, (z - zLow) / zRange));
    positions[index * 3] = (rawPositions[index * 3] - center[0]) / scales[0];
    positions[index * 3 + 1] = -(rawPositions[index * 3 + 1] - center[1]) / scales[1];
    positions[index * 3 + 2] = (z - center[2]) / scales[2] * 0.9;
    colors[index * 3] = 0.04 + depthMix * 0.18;
    colors[index * 3 + 1] = 0.18 + depthMix * 0.78;
    colors[index * 3 + 2] = 0.08 + depthMix * 0.12;
  }
  return { count, positions, colors };
}

function clampColor(value) {
  if (!Number.isFinite(value)) return 255;
  return Math.max(0, Math.min(255, value));
}

function uploadPointcloud(cloud) {
  const viewer = state.viewer;
  if (!viewer) return;
  const { gl } = viewer;
  viewer.count = cloud.count;
  resetPointcloudView(false);
  gl.bindBuffer(gl.ARRAY_BUFFER, viewer.positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, cloud.positions, gl.STATIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, viewer.colorBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, cloud.colors, gl.STATIC_DRAW);
  resizePointcloudCanvas();
  schedulePointcloudDraw();
}

function clearPointcloudViewer() {
  const viewer = state.viewer;
  $(".pointcloud-box")?.classList.remove("viewer-ready");
  setText("pointcloudStatus", "后续建模结果展示区");
  setText("pointcloudHint", "生成模型后可拖拽旋转，滚轮缩放");
  if (!viewer) return;
  viewer.count = 0;
  viewer.gl.clear(viewer.gl.COLOR_BUFFER_BIT | viewer.gl.DEPTH_BUFFER_BIT);
}

function resetPointcloudView(draw = true) {
  const viewer = state.viewer;
  if (!viewer) return;
  viewer.rotationX = -0.62;
  viewer.rotationY = 0.78;
  viewer.zoom = 3.2;
  if (draw) schedulePointcloudDraw();
}

function resizePointcloudCanvas() {
  const viewer = state.viewer;
  if (!viewer) return;
  const rect = viewer.canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.floor(rect.width * dpr));
  const height = Math.max(1, Math.floor(rect.height * dpr));
  if (viewer.canvas.width !== width || viewer.canvas.height !== height) {
    viewer.canvas.width = width;
    viewer.canvas.height = height;
    viewer.gl.viewport(0, 0, width, height);
  }
  schedulePointcloudDraw();
}

function schedulePointcloudDraw() {
  const viewer = state.viewer;
  if (!viewer || viewer.frame) return;
  viewer.frame = window.requestAnimationFrame(() => {
    viewer.frame = 0;
    drawPointcloud();
  });
}

function drawPointcloud() {
  const viewer = state.viewer;
  if (!viewer) return;
  const { gl, program, canvas } = viewer;
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  if (!viewer.count) return;

  gl.useProgram(program);
  const aspect = canvas.width / Math.max(canvas.height, 1);
  const projection = mat4Perspective(Math.PI / 4, aspect, 0.1, 100);
  let modelView = mat4Identity();
  modelView = mat4Translate(modelView, [0, 0, -viewer.zoom]);
  modelView = mat4RotateX(modelView, viewer.rotationX);
  modelView = mat4RotateY(modelView, viewer.rotationY);
  const mvp = mat4Multiply(projection, modelView);

  const positionLoc = gl.getAttribLocation(program, "aPosition");
  gl.bindBuffer(gl.ARRAY_BUFFER, viewer.positionBuffer);
  gl.enableVertexAttribArray(positionLoc);
  gl.vertexAttribPointer(positionLoc, 3, gl.FLOAT, false, 0, 0);

  const colorLoc = gl.getAttribLocation(program, "aColor");
  gl.bindBuffer(gl.ARRAY_BUFFER, viewer.colorBuffer);
  gl.enableVertexAttribArray(colorLoc);
  gl.vertexAttribPointer(colorLoc, 3, gl.FLOAT, false, 0, 0);

  gl.uniformMatrix4fv(gl.getUniformLocation(program, "uMvp"), false, mvp);
  gl.drawArrays(gl.POINTS, 0, viewer.count);
}

function mat4Identity() {
  return new Float32Array([
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ]);
}

function mat4Perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  const nf = 1 / (near - far);
  return new Float32Array([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (far + near) * nf, -1,
    0, 0, 2 * far * near * nf, 0,
  ]);
}

function mat4Multiply(a, b) {
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col += 1) {
    for (let row = 0; row < 4; row += 1) {
      out[col * 4 + row] =
        a[0 * 4 + row] * b[col * 4 + 0] +
        a[1 * 4 + row] * b[col * 4 + 1] +
        a[2 * 4 + row] * b[col * 4 + 2] +
        a[3 * 4 + row] * b[col * 4 + 3];
    }
  }
  return out;
}

function mat4Translate(matrix, vector) {
  const out = new Float32Array(matrix);
  out[12] = matrix[0] * vector[0] + matrix[4] * vector[1] + matrix[8] * vector[2] + matrix[12];
  out[13] = matrix[1] * vector[0] + matrix[5] * vector[1] + matrix[9] * vector[2] + matrix[13];
  out[14] = matrix[2] * vector[0] + matrix[6] * vector[1] + matrix[10] * vector[2] + matrix[14];
  out[15] = matrix[3] * vector[0] + matrix[7] * vector[1] + matrix[11] * vector[2] + matrix[15];
  return out;
}

function mat4RotateX(matrix, rad) {
  const s = Math.sin(rad);
  const c = Math.cos(rad);
  const rotation = new Float32Array([
    1, 0, 0, 0,
    0, c, s, 0,
    0, -s, c, 0,
    0, 0, 0, 1,
  ]);
  return mat4Multiply(matrix, rotation);
}

function mat4RotateY(matrix, rad) {
  const s = Math.sin(rad);
  const c = Math.cos(rad);
  const rotation = new Float32Array([
    c, 0, -s, 0,
    0, 1, 0, 0,
    s, 0, c, 0,
    0, 0, 0, 1,
  ]);
  return mat4Multiply(matrix, rotation);
}

function setPill(id, text, className) {
  const pill = document.getElementById(id);
  if (!pill) return;
  pill.textContent = text;
  pill.className = className || "";
}

function setStepStatus(key, status) {
  document.querySelectorAll(`[data-step-key="${key}"]`).forEach((button) => {
    button.dataset.status = status;
  });
}

function setCurrentStep(key) {
  document.querySelectorAll(".task-step").forEach((button) => {
    button.classList.toggle("active", button.dataset.stepKey === key);
  });
}

function getModuleLayoutMode(view) {
  return moduleLayoutModes[view] || "capture";
}

function applyModuleLayout(view) {
  const mode = getModuleLayoutMode(view);
  const stage = $(".stage");
  document.body.classList.toggle("layout-capture", mode === "capture");
  document.body.classList.toggle("layout-analysis", mode === "analysis");
  stage?.classList.toggle("layout-capture", mode === "capture");
  stage?.classList.toggle("layout-analysis", mode === "analysis");
  stage?.setAttribute("data-layout-mode", mode);
}

function switchView(view, stepKey = null) {
  applyModuleLayout(view);
  document.querySelectorAll(".view-page").forEach((page) => {
    page.classList.toggle("active", page.dataset.page === view);
  });
  setText("viewTitle", titles[view] || "功能模块");
  if (stepKey) setCurrentStep(stepKey);
  addLog(`切换到 ${titles[view] || view}`);
}

async function runDeviceTest(type) {
  if (type === "motor" && state.hardwareStatus.connected) {
    await runHardwareSelfTest(true);
    return;
  }
  const map = {
    motor: {
      pill: "motorStatus",
      text: "电机: 离线自检通过",
      step: "motor",
      log: "电机检测完成：旋转平台、升降机构为模拟通过状态。",
    },
    light: {
      pill: "lightStatus",
      text: "光源: 离线自检通过",
      step: "light",
      log: "光源检测完成：370-940nm 波段为模拟通过状态。",
    },
    camera: {
      pill: "cameraStatus",
      text: "相机: 尚未接入",
      step: "camera",
      status: "warning",
      prepValue: false,
      log: "相机检查完成：RGB 已进入 OpenCV/DirectShow 接入层；多光谱相机等待 DVP2 SDK，不能开始真实采集。",
    },
  };
  const item = map[type];
  if (!item) return;
  setPill(item.pill, item.text, item.status === "warning" ? "warn" : "ok");
  setStepStatus(item.step, item.status === "warning" ? "warning" : "done");
  state.devicePrep[type] = item.prepValue ?? true;
  setText("statusNote", "硬件通信尚未接入，当前自检结果来自离线模拟。");
  addLog(item.log);
  await syncDevicePreparation();
}

function resetCaptureStepStatuses() {
  ["sample", "dark", "white", "rgb", "spectral", "integrity"].forEach((key) => setStepStatus(key, "waiting"));
  if ($("#captureProgress")) $("#captureProgress").style.width = "0%";
  setText("captureProgressText", "采集进度: 0 / 4");
  setText("captureSaveStatus", "等待采集完成");
  renderCaptureRotationStatus();
}

function renderCalibrationStatus() {
  const passed = state.calibrationStatus === "passed";
  const button = $("#confirmCalibration");
  if (button) {
    button.textContent = passed ? "✓ 检查通过" : "确认检查通过";
    button.classList.toggle("passed", passed);
  }
  setText("calibrationConfirmText", passed
    ? "已由操作员人工确认标定检查通过。"
    : "未确认。本按钮仅记录人工检查结果，不代表已连接真实标定设备。");
  setStepStatus("calibration", passed ? "done" : "waiting");
}

async function confirmCalibrationCheck() {
  state.calibrationStatus = "passed";
  state.devicePrep.calibration = true;
  renderCalibrationStatus();
  addLog("标定检查已人工确认通过。");
  await syncDevicePreparation();
}

async function updateCaptureProgress(step) {
  if (!requireDevicePreparation()) return;
  if (!requireActiveSample()) return;
  state.captureStarted = true;
  lockRotationSettings();
  state.captureStep = Math.max(state.captureStep, step);
  renderSystemStatus();
  const percent = Math.min(100, state.captureStep * 25);
  const progress = $("#captureProgress");
  if (progress) progress.style.width = `${percent}%`;
  setText("captureProgressText", `采集进度: ${Math.min(4, state.captureStep)} / 4`);
  ["sample", "dark", "white", "rgb", "spectral", "integrity"].slice(0, state.captureStep + 1).forEach((key) => setStepStatus(key, "done"));
  renderCurrentSample();
  addLog(`样品采集步骤 ${step} 已完成（离线模拟）。`);
  if (step >= 4) {
    await completeCurrentCapture();
  }
}

async function completeCurrentCapture() {
  if (!requireDevicePreparation()) return;
  if (!requireActiveSample()) return;
  if (state.captureCompleting) return;
  state.captureCompleting = true;
  state.captureStarted = true;
  renderSystemStatus();
  const button = $("#enterAnalysisFromCapture");
  try {
    setText("captureSaveStatus", "正在保存本次拍摄数据...");
    const rotationSettings = collectRotationPayload();
    if (!rotationSettings) {
      setText("captureSaveStatus", "旋转拍摄设置无效");
      return;
    }
    const payload = await api("/api/complete-capture", {
      method: "POST",
      body: JSON.stringify({
        sampleId: $("#sampleId")?.value || "",
        sampleRotation: rotationSettings,
        rgbDirName: state.rgbDirName || "rgb",
        multispectralDirName: state.multispectralDirName || "multispectral",
      }),
    });
    state.currentCaptureDir = payload.currentCaptureDir || "";
    state.currentCaptureValid = Boolean(state.currentCaptureDir);
    state.analysisDataDir = payload.analysisDataDir || state.currentCaptureDir;
    applyImageDirNames({
      rgbDirName: payload.rgbDirName || payload.colorDir || state.rgbDirName,
      multispectralDirName: payload.multispectralDirName || payload.depthDir || state.multispectralDirName,
      otherImageDirs: [],
    });
    state.captureRotationPlan = payload.captureRotationPlan || state.captureRotationPlan;
    renderRotationPlan(state.captureRotationPlan);
    setText("captureSaveStatus", state.currentCaptureValid ? `本次拍摄已保存: ${state.currentCaptureDir}` : "本次拍摄数据未生成");
    if (button) button.disabled = !state.analysisDataDir;
    renderCurrentSample();
    updateCurrentCaptureControls();
    addLog(`本次拍摄目录已写入: ${state.currentCaptureDir}`);
    if (state.currentCaptureValid) {
      try {
        await loadSampleFolder(state.currentCaptureDir, { source: "current" });
      } catch (loadError) {
        setText("captureSaveStatus", `本次拍摄已保存，但自动加载失败: ${state.currentCaptureDir}`);
        addLog(loadError.message || "本次拍摄目录自动加载失败。", "ERROR");
      }
    }
  } catch (error) {
    state.currentCaptureValid = false;
    setText("captureSaveStatus", "本次拍摄保存失败");
    if (button) button.disabled = true;
    updateCurrentCaptureControls();
    addLog(error.message || "本次拍摄保存失败。", "ERROR");
  } finally {
    state.captureCompleting = false;
    renderSystemStatus();
  }
}

async function enterAnalysisFromCapture() {
  if (!requireDevicePreparation()) return;
  if (!requireActiveSample()) return;
  if (!state.currentCaptureValid || !state.currentCaptureDir) {
    await completeCurrentCapture();
  }
  if (!state.currentCaptureValid || !state.currentCaptureDir) {
    addLog("没有可进入分析的本次拍摄数据。", "WARN");
    return;
  }
  switchView("shape", "load-rgbd");
  await loadSampleFolder(state.currentCaptureDir, { source: "current" });
}

function qualityPayload() {
  return {
    datasetDir: state.analysisDataDir,
    colorDir: state.rgbDirName || $("#colorDir")?.value || "rgb",
    depthDir: state.multispectralDirName || $("#depthDir")?.value || "multispectral",
    rgbDirName: state.rgbDirName || $("#colorDir")?.value || "rgb",
    multispectralDirName: state.multispectralDirName || $("#depthDir")?.value || "multispectral",
    sampleId: $("#sampleId")?.value || "",
    fruitType: state.fruitType || "",
    variety: state.variety || "generic",
    selectedSscModelId: $("#sscModelSelect")?.value || state.selectedSscModelId || "",
    selectedTaModelId: $("#taModelSelect")?.value || state.selectedTaModelId || "",
    selectedPhModelId: $("#phModelSelect")?.value || state.selectedPhModelId || "",
  };
}

function updateSampleSessionFromReport(report = {}) {
  applyLoadedSampleMetadata(report.sampleMetadata || report.metadata || {});
  state.dataCheck = {
    status: report.status || "empty",
    rgbCount: Number(report.rgbCount || 0),
    spectralCount: Number(report.spectralCount || 0),
    pairCount: Number(report.pairCount || 0),
  };
  state.sampleSession.sampleId = $("#sampleId")?.value || "--";
  state.sampleSession.analysisDataDir = state.analysisDataDir || report.datasetDir || "";
  renderQualitySampleSummary();
}

function applyLoadedSampleMetadata(metadata = {}) {
  const fruitType = metadata.fruit_type || metadata.fruitType || "";
  const variety = metadata.variety || "";
  const sampleName = metadata.sample_name || metadata.sampleName || "";
  const sampleId = metadata.sample_id || metadata.sampleId || "";
  const imageDirs = metadata.image_directories || {};
  if (imageDirs.rgb || imageDirs.multispectral) {
    applyImageDirNames({
      rgbDirName: imageDirs.rgb || state.rgbDirName || "rgb",
      multispectralDirName: imageDirs.multispectral || state.multispectralDirName || "multispectral",
      otherImageDirs: state.otherImageDirs,
    });
  }
  if (fruitType) state.fruitType = fruitType;
  if (variety) state.variety = variety;
  if (state.hasSample && !state.sampleName && sampleName) state.sampleName = sampleName;
  if (state.hasSample && !state.sampleId && sampleId) state.sampleId = sampleId;
  state.sampleSession.fruitType = state.fruitType;
  state.sampleSession.variety = state.variety;
  if ($("#qualityFruitType") && fruitType) $("#qualityFruitType").value = fruitType;
  if ($("#qualityVariety") && variety) $("#qualityVariety").value = variety;
  if (fruitType || variety) {
    renderCurrentSample();
    loadQualityModels().catch((error) => addLog(error.message, "WARN"));
  }
}

function updateSampleSessionFromImages() {
  state.sampleSession.rgbFiles = state.imageBrowser.images
    .map((item) => item.color?.name)
    .filter(Boolean);
  state.sampleSession.multispectralFiles = state.imageBrowser.images
    .map((item) => item.depth?.name)
    .filter(Boolean);
  renderQualitySampleSummary();
}

function applyBackendSampleSession(sample = {}) {
  state.sampleSession = {
    ...state.sampleSession,
    sampleId: sample.sample_id || state.sampleSession.sampleId || $("#sampleId")?.value || "--",
    sampleName: sample.sample_name || state.sampleName || state.sampleSession.sampleName || "--",
    analysisDataDir: sample.analysis_data_dir || state.analysisDataDir || "",
    rgbFiles: Array.isArray(sample.rgb_files) ? sample.rgb_files : state.sampleSession.rgbFiles,
    multispectralFiles: Array.isArray(sample.multispectral_files) ? sample.multispectral_files : state.sampleSession.multispectralFiles,
    captureTime: sample.capture_time || state.sampleSession.captureTime || "",
    fruitType: sample.fruit_type || state.fruitType || "",
    variety: sample.variety || state.variety || "generic",
    selectedSscModelId: sample.selected_ssc_model_id || state.selectedSscModelId || "",
    selectedTaModelId: sample.selected_ta_model_id || state.selectedTaModelId || "",
    selectedPhModelId: sample.selected_ph_model_id || state.selectedPhModelId || "",
    sscResult: sample.ssc_result || state.sampleSession.sscResult,
    taResult: sample.ta_result || state.sampleSession.taResult,
    phResult: sample.ph_result || state.sampleSession.phResult,
  };
  renderQualitySampleSummary();
}

function renderQualitySampleSummary() {
  const sampleId = state.sampleSession.sampleId || $("#sampleId")?.value || "--";
  const dir = state.sampleSession.analysisDataDir || state.analysisDataDir || "未加载样品数据";
  const rgbCount = state.dataCheck.rgbCount || state.sampleSession.rgbFiles.length || 0;
  const spectralCount = state.dataCheck.spectralCount || state.sampleSession.multispectralFiles.length || 0;
  const pairCount = state.dataCheck.pairCount || Math.min(rgbCount, spectralCount);
  const dataStatus = state.dataCheck.status === "complete"
    ? "完整"
    : state.dataCheck.status === "incomplete"
      ? "不完整"
      : state.analysisDataDir
        ? "待检查"
        : "未加载";

  ["ssc", "acid"].forEach((prefix) => {
    setText(`${prefix}SampleId`, sampleId);
    setText(`${prefix}SamplePath`, dir);
    setText(`${prefix}RgbCount`, rgbCount);
    setText(`${prefix}SpectralCount`, spectralCount);
    setText(`${prefix}DataStatus`, pairCount ? dataStatus : "未加载");
    setText(`${prefix}SampleCount`, pairCount);
  });
}

function modelOption(model) {
  const name = model.display_name || model.model_name || model.model_id;
  const meta = `${model.model_type || ""} ${model.preprocessing || ""} ${model.version || ""}`.trim();
  const mark = model.status === "Default" || model.is_default ? "默认" : "已发布";
  return `<option value="${escapeHtml(model.model_id)}">${escapeHtml(name)} · ${escapeHtml(meta)} · ${mark}</option>`;
}

function fillPlainSelect(selector, values, selectedValue, fallback = "") {
  const select = $(selector);
  if (!select) return "";
  const options = (values || []).filter(Boolean);
  if (!options.length && fallback) options.push(fallback);
  select.innerHTML = options.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  const target = selectedValue && options.includes(selectedValue) ? selectedValue : options[0] || "";
  select.value = target;
  return target;
}

function fillModelSelect(selector, models, selectedId, defaultModel = null) {
  const select = $(selector);
  if (!select) return;
  if (!models || !models.length) {
    select.innerHTML = `<option value="">无兼容模型</option>`;
    select.value = "";
    return;
  }
  select.innerHTML = `${(models || []).map(modelOption).join("")}`;
  const target = selectedId || defaultModel?.model_id || "";
  if (target && [...select.options].some((option) => option.value === target)) select.value = target;
}

function modelById(models = [], modelId = "") {
  return (models || []).find((model) => model.model_id === modelId) || null;
}

function selectedModelFromCatalog(target, selectedId = "") {
  const catalog = state.modelCatalog || {};
  const models = catalog[target] || [];
  return modelById(models, selectedId) || catalog.defaults?.[target] || models[0] || null;
}

function isGenericModelForCurrentVariety(model = null) {
  const currentVariety = String(state.variety || "generic").trim().toLowerCase() || "generic";
  const modelVariety = String(model?.variety || "generic").trim().toLowerCase() || "generic";
  return currentVariety !== "generic" && modelVariety === "generic";
}

function renderModelSummaryRow(id, target, selectedId = "") {
  const row = document.getElementById(id);
  if (!row) return;
  const model = selectedModelFromCatalog(target, selectedId);
  const label = row.querySelector("strong");
  if (!model) {
    row.dataset.status = "missing";
    if (label) label.textContent = "暂无正式模型";
    return;
  }
  if (isGenericModelForCurrentVariety(model)) {
    row.dataset.status = "generic";
    if (label) label.textContent = "正在使用通用模型";
    return;
  }
  const isDefault = model.status === "Default" || model.is_default;
  row.dataset.status = "ready";
  if (label) label.textContent = isDefault ? "默认模型已配置" : "已发布模型已配置";
}

function renderModelOverview() {
  setText("modelOverviewScope", `${state.fruitType || "未选择水果"} / ${state.variety || "generic"}`);
  renderModelSummaryRow("modelSummarySsc", "ssc", state.selectedSscModelId);
  renderModelSummaryRow("modelSummaryTa", "ta", state.selectedTaModelId);
  renderModelSummaryRow("modelSummaryPh", "ph", state.selectedPhModelId);
  const missing = ["modelSummarySsc", "modelSummaryTa", "modelSummaryPh"]
    .some((id) => document.getElementById(id)?.dataset.status === "missing");
  const generic = ["modelSummarySsc", "modelSummaryTa", "modelSummaryPh"]
    .some((id) => document.getElementById(id)?.dataset.status === "generic");
  const modeText = state.modelAdvanced ? "高级模式已展开，可以手动更换已发布模型。" : "普通模式会优先使用 Default 模型。";
  const suffix = missing
    ? "缺失模型不会生成示例数值。"
    : generic
      ? "部分指标正在使用通用品种模型。"
      : "当前指标已有正式模型。";
  setText("modelOverviewHint", `${modeText} ${suffix}`);
  const button = $("#toggleModelAdvanced");
  if (button) button.textContent = state.modelAdvanced ? "隐藏高级" : "更换模型";
  document.body.classList.toggle("model-advanced", state.modelAdvanced);
}

function toggleModelAdvanced() {
  state.modelAdvanced = !state.modelAdvanced;
  renderModelOverview();
}

async function loadSampleTypeCatalog() {
  const selectedFruit = $("#qualityFruitType")?.value.trim() || state.fruitType || "";
  const selectedVariety = $("#qualityVariety")?.value.trim() || state.variety || "generic";
  const payload = await api(`/api/quality-models?fruitType=${encodeURIComponent(selectedFruit)}&variety=${encodeURIComponent(selectedVariety)}`);
  const fruitType = fillPlainSelect("#qualityFruitType", payload.fruitTypes || [], selectedFruit);
  const varietyPayload = await api(`/api/quality-models?fruitType=${encodeURIComponent(fruitType)}&variety=${encodeURIComponent(selectedVariety)}`);
  fillPlainSelect("#qualityVariety", varietyPayload.varieties || ["generic"], selectedVariety, "generic");
  return varietyPayload;
}

async function loadQualityModels() {
  let fruitType = state.fruitType || "";
  let variety = state.variety || "generic";
  if (!fruitType) {
    const catalog = await api("/api/quality-models");
    fruitType = catalog.fruitTypes?.[0] || $("#qualityFruitType")?.value.trim() || "";
    variety = catalog.varieties?.[0] || "generic";
  }
  const payload = await api(`/api/quality-models?fruitType=${encodeURIComponent(fruitType)}&variety=${encodeURIComponent(variety)}`);
  state.modelCatalog = payload;
  if (!hasActiveSample()) {
    fillPlainSelect("#qualityFruitType", payload.fruitTypes || [], fruitType);
    fillPlainSelect("#qualityVariety", payload.varieties || ["generic"], variety, "generic");
  }
  fillModelSelect("#sscModelSelect", payload.ssc, state.selectedSscModelId, payload.defaults?.ssc);
  fillModelSelect("#taModelSelect", payload.ta, state.selectedTaModelId, payload.defaults?.ta);
  fillModelSelect("#phModelSelect", payload.ph, state.selectedPhModelId, payload.defaults?.ph);
  state.selectedSscModelId = $("#sscModelSelect")?.value || "";
  state.selectedTaModelId = $("#taModelSelect")?.value || "";
  state.selectedPhModelId = $("#phModelSelect")?.value || "";
  updateAnalysisButtonStates();
  renderModelOverview();
  return payload;
}

async function saveModelSelection() {
  const payload = qualityPayload();
  state.fruitType = payload.fruitType;
  state.variety = payload.variety;
  state.selectedSscModelId = payload.selectedSscModelId;
  state.selectedTaModelId = payload.selectedTaModelId;
  state.selectedPhModelId = payload.selectedPhModelId;
  await api("/api/model-selection", { method: "POST", body: JSON.stringify(payload) });
}

function openSampleModal() {
  const modal = $("#sampleModal");
  if (!modal) return;
  modal.hidden = false;
  loadNewSampleCatalog().catch((error) => setText("newSampleHint", error.message));
}

function closeSampleModal() {
  const modal = $("#sampleModal");
  if (modal) modal.hidden = true;
}

async function loadNewSampleCatalog() {
  const selectedFruit = $("#newSampleFruitType")?.value || state.fruitType || "";
  const selectedVariety = $("#newSampleVariety")?.value || state.variety || "generic";
  const payload = await api(`/api/quality-models?fruitType=${encodeURIComponent(selectedFruit)}&variety=${encodeURIComponent(selectedVariety)}`);
  const fruitType = fillPlainSelect("#newSampleFruitType", payload.fruitTypes || [], selectedFruit);
  const varietyPayload = await api(`/api/quality-models?fruitType=${encodeURIComponent(fruitType)}&variety=${encodeURIComponent(selectedVariety)}`);
  fillPlainSelect("#newSampleVariety", varietyPayload.varieties || ["generic"], selectedVariety, "generic");
  setText("newSampleHint", varietyPayload.fruitTypes?.length ? "样品种类和品种将保存到本次样品 metadata.json。" : "暂无 Published / Default 模型，请先在 Model Studio 发布模型。");
}

async function createNewSample() {
  if (!requireDevicePreparation()) return;
  const sampleName = $("#captureSampleName")?.value.trim() || $("#newSampleName")?.value.trim() || "";
  if (!sampleName) {
    setText("newSampleHint", "样品名称必须填写。");
    setText("sampleCreateStatus", "请填写样品名称");
    return;
  }
  const saveRootDir = $("#saveRootDir")?.value.trim() || state.saveRootDir || "";
  if (!saveRootDir) {
    setText("newSampleHint", "请选择样品保存位置。");
    setText("sampleCreateStatus", "请选择保存位置");
    return;
  }
  const rotationSettings = collectRotationPayload();
  if (!rotationSettings) {
    setText("sampleCreateStatus", "旋转设置无效");
    setText("newSampleHint", "期望角度间隔必须大于 0。");
    return;
  }
  setText("sampleCreateStatus", "正在创建样品");
  await loadSampleTypeCatalog().catch((error) => addLog(error.message, "WARN"));
  const payload = {
    sampleName,
    saveRootDir,
    fruitType: $("#qualityFruitType")?.value || $("#newSampleFruitType")?.value || "",
    variety: $("#qualityVariety")?.value || $("#newSampleVariety")?.value || "generic",
    sampleRotation: rotationSettings,
    rgbDirName: state.rgbDirName || "rgb",
    multispectralDirName: state.multispectralDirName || "multispectral",
  };
  const response = await api("/api/new-sample", { method: "POST", body: JSON.stringify(payload) });
  applySampleSessionState(response.sample || {});
  clearSampleDependentState();
  applySampleSessionState(response.sample || {});
  setStepStatus("sample", "done");
  await loadQualityModels().catch((error) => addLog(error.message, "WARN"));
  renderCurrentSample();
  closeSampleModal();
  addLog(`已创建当前样品：${state.sampleName}`);
}

function applySampleSessionState(sample = {}) {
  state.hasSample = Boolean(sample.hasSample);
  state.sampleId = sample.sampleId || "";
  state.sampleName = sample.sampleName || "";
  state.sampleCreatedAt = sample.createdAt || "";
  state.saveRootDir = sample.saveRootDir || "";
  state.currentCaptureDir = sample.currentCaptureDir || "";
  state.currentCaptureValid = Boolean(sample.currentCaptureValid && state.currentCaptureDir);
  state.analysisDataDir = sample.analysisDataDir || "";
  applyImageDirNames({
    rgbDirName: sample.rgbDirName || sample.colorDir || state.rgbDirName || "rgb",
    multispectralDirName: sample.multispectralDirName || sample.depthDir || state.multispectralDirName || "multispectral",
    otherImageDirs: sample.otherImageDirs || state.otherImageDirs || [],
  });
  state.captureStarted = Boolean(sample.captureStarted);
  state.fruitType = sample.fruitType || "";
  state.variety = sample.variety || "generic";
  state.selectedSscModelId = sample.selectedSscModelId || "";
  state.selectedTaModelId = sample.selectedTaModelId || "";
  state.selectedPhModelId = sample.selectedPhModelId || "";
  state.captureRotationPlan = sample.captureRotationPlan || state.captureRotationPlan || buildCaptureRotationPlan(DEFAULT_ROTATION_SETTINGS);
  state.sampleSession.sampleId = state.sampleId;
  state.sampleSession.sampleName = state.sampleName;
  state.sampleSession.fruitType = state.fruitType;
  state.sampleSession.variety = state.variety;
  if ($("#qualityFruitType")) $("#qualityFruitType").value = state.fruitType;
  if ($("#qualityVariety")) $("#qualityVariety").value = state.variety;
  renderRotationPlan(state.captureRotationPlan);
}

async function chooseSaveRoot() {
  if (state.captureStarted) {
    setText("sampleCreateStatus", "采集已开始，保存位置已锁定");
    addLog("采集开始后不能修改保存位置。", "WARN");
    return;
  }
  try {
    const payload = await selectFolderPath({ purpose: "save", initial: state.saveRootDir || $("#saveRootDir")?.value || "" });
    const selected = payload.path || payload.saveRootDir || "";
    if (selected) {
      const dirs = await openImageDirSettingsModal(imageDirSettingsDefaults());
      if (!dirs) {
        addLog("用户取消图像目录名称设置，已保留原保存位置。", "WARN");
        return;
      }
      applyImageDirNames(dirs);
      state.saveRootDir = selected;
      setPathDisplay("#saveRootDir", selected);
      setText("sampleCreateStatus", "保存位置和图像目录已选择");
      addLog(`样品保存位置已选择: ${selected}；RGB=${state.rgbDirName}，多光谱=${state.multispectralDirName}`);
    }
  } catch (error) {
    if (error.payload?.cancelled) {
      addLog("用户取消选择保存位置，已保留原路径。", "WARN");
      return;
    }
    setText("sampleCreateStatus", "保存位置无效");
    addLog(error.message || "选择保存位置失败。", "WARN");
  }
}

async function openCaptureFolder() {
  if (!requireActiveSample()) return;
  if (!state.currentCaptureDir) {
    addLog("当前样品目录尚未生成。", "WARN");
    return;
  }
  try {
    await api("/api/open-folder", {
      method: "POST",
      body: JSON.stringify({ path: state.currentCaptureDir }),
    });
  } catch (error) {
    addLog(error.message || "打开样品文件夹失败。", "ERROR");
  }
}

function renderSscResult(result = {}) {
  const hasValue = Number.isFinite(result.value);
  state.ssc = hasValue ? Number(result.value) : null;
  setText("sscValue", hasValue ? Number(result.value).toFixed(2) : "--");
  setText("resultSsc", hasValue ? `${Number(result.value).toFixed(2)} °Brix` : "--");
  setText("sscConfidence", Number.isFinite(result.confidence) ? `${Math.round(result.confidence * 100)}%` : "--");
  setText("sscElapsed", Number.isFinite(result.elapsed_time) ? `${result.elapsed_time}s` : "--");
  setText("sscModelName", result.model_name || "SSC 预测模型");
  setText("sscModelVersion", [result.model_version, result.model_type, result.preprocessing].filter(Boolean).join(" · ") || "未接入");
  setText("sscModelStatus", ["ok", "success"].includes(result.status) ? "预测完成" : "模型预测待接入");
  setText("sscMessage", result.error_message || (hasValue ? "预测完成" : "暂无预测结果"));
  renderSystemStatus();
}

function renderAcidResult(taResult = {}, phResult = {}) {
  const hasTa = Number.isFinite(taResult.value);
  const hasPh = Number.isFinite(phResult.value);
  state.ta = hasTa ? Number(taResult.value) : null;
  state.ph = hasPh ? Number(phResult.value) : null;
  setText("taValue", hasTa ? Number(taResult.value).toFixed(2) : "--");
  setText("phValue", hasPh ? Number(phResult.value).toFixed(2) : "--");
  setText("resultTa", hasTa ? `${Number(taResult.value).toFixed(2)} %` : "--");
  setText("resultPh", hasPh ? Number(phResult.value).toFixed(2) : "--");
  setText("acidConfidence", Number.isFinite(taResult.confidence) ? `${Math.round(taResult.confidence * 100)}%` : "--");
  setText("acidElapsed", Number.isFinite(taResult.elapsed_time) ? `${taResult.elapsed_time}s` : "--");
  setText("acidModelName", taResult.model_name || "TA / pH 预测模型");
  setText("acidModelVersion", [taResult.model_version || phResult.model_version, taResult.model_type || phResult.model_type, taResult.preprocessing || phResult.preprocessing].filter(Boolean).join(" · ") || "未接入");
  setText("acidModelStatus", ["ok", "success"].includes(taResult.status) || ["ok", "success"].includes(phResult.status) ? "预测完成" : "模型预测待接入");
  setText("acidMessage", taResult.error_message || phResult.error_message || (hasTa || hasPh ? "预测完成" : "暂无预测结果"));
  renderSystemStatus();
}

function clearTasteResult() {
  state.ratio = null;
  state.grade = null;
  setText("resultRatio", "--");
  setText("gradeValue", "--");
  setText("tasteRatio", "--");
  setText("tasteGrade", "--");
  setText("tasteGradeLarge", "--");
  setText("tasteExplain", "等待糖度与酸度数据。");
  setStepStatus("ratio", "waiting");
  setStepStatus("rating", "waiting");
  renderSystemStatus();
}

function clearSscPrediction() {
  renderSscResult({});
  state.sampleSession.sscResult = null;
  clearTasteResult();
  setStepStatus("sugar", "waiting");
}

function clearAcidPrediction() {
  renderAcidResult({}, {});
  state.sampleSession.taResult = null;
  state.sampleSession.phResult = null;
  clearTasteResult();
  setStepStatus("acid", "waiting");
}

function clearAllPredictions() {
  clearSscPrediction();
  clearAcidPrediction();
}

async function runSscAnalysis() {
  if (!requireActiveSample()) return;
  if (!state.selectedSscModelId) {
    addLog("当前样品没有兼容的 SSC 模型。", "WARN");
    setText("sscModelStatus", "无兼容模型");
    return;
  }
  if (!state.analysisDataDir) {
    addLog("请先在形态分析页面加载当前样品数据。", "WARN");
    setStepStatus("sugar", "warning");
    renderQualitySampleSummary();
    return;
  }
  const button = $("#startSscAnalysis");
  if (button) button.disabled = true;
  state.systemTask = "ssc";
  renderSystemStatus();
  try {
    await saveModelSelection();
    setText("sscModelStatus", "正在检查样品数据");
    const payload = await api("/api/predict-ssc", {
      method: "POST",
      body: JSON.stringify(qualityPayload()),
    });
    if (payload.dataCheck) updateSampleSessionFromReport(payload.dataCheck);
    if (payload.sample) applyBackendSampleSession(payload.sample);
    renderSscResult(payload.result || {});
    const ok = ["ok", "success"].includes(payload.result?.status);
    setStepStatus("sugar", ok ? "done" : "warning");
    addLog(payload.result?.error_message || "SSC 预测接口已返回结果。", ok ? "INFO" : "WARN");
  } catch (error) {
    setText("sscModelStatus", "预测失败");
    setText("sscMessage", error.message || "SSC 预测失败");
    setStepStatus("sugar", "failed");
    addLog(error.message || "SSC 预测失败。", "ERROR");
  } finally {
    if (state.systemTask === "ssc") state.systemTask = "";
    updateAnalysisButtonStates();
    renderSystemStatus();
  }
}

async function runAcidAnalysis() {
  if (!requireActiveSample()) return;
  if (!state.selectedTaModelId && !state.selectedPhModelId) {
    addLog("当前样品没有兼容的 TA / pH 模型。", "WARN");
    setText("acidModelStatus", "无兼容模型");
    return;
  }
  if (!state.analysisDataDir) {
    addLog("请先在形态分析页面加载当前样品数据。", "WARN");
    setStepStatus("acid", "warning");
    renderQualitySampleSummary();
    return;
  }
  const button = $("#startAcidAnalysis");
  if (button) button.disabled = true;
  state.systemTask = "acid";
  renderSystemStatus();
  try {
    await saveModelSelection();
    setText("acidModelStatus", "正在检查样品数据");
    const payload = await api("/api/predict-acid", {
      method: "POST",
      body: JSON.stringify(qualityPayload()),
    });
    if (payload.dataCheck) updateSampleSessionFromReport(payload.dataCheck);
    if (payload.sample) applyBackendSampleSession(payload.sample);
    renderAcidResult(payload.taResult || {}, payload.phResult || {});
    const ok = ["ok", "success"].includes(payload.taResult?.status) || ["ok", "success"].includes(payload.phResult?.status);
    setStepStatus("acid", ok ? "done" : "warning");
    addLog(payload.taResult?.error_message || payload.phResult?.error_message || "酸度预测接口已返回结果。", ok ? "INFO" : "WARN");
  } catch (error) {
    setText("acidModelStatus", "预测失败");
    setText("acidMessage", error.message || "酸度预测失败");
    setStepStatus("acid", "failed");
    addLog(error.message || "酸度预测失败。", "ERROR");
  } finally {
    if (state.systemTask === "acid") state.systemTask = "";
    updateAnalysisButtonStates();
    renderSystemStatus();
  }
}

function updateTaste(announce = true) {
  if (!Number.isFinite(state.ssc) || !Number.isFinite(state.ta) || state.ta <= 0) {
    if (announce) addLog("口感分析需要等待糖度与酸度模型预测结果。", "WARN");
    return;
  }
  const ratio = state.ssc / state.ta;
  state.ratio = ratio;
  state.grade = ratio >= 25 && state.ssc >= 11 ? "A" : ratio >= 18 ? "B" : "C";
  setText("resultRatio", ratio.toFixed(2));
  setText("gradeValue", state.grade);
  setText("tasteGradeLarge", state.grade);
  setText("tasteExplain", `SSC ${state.ssc.toFixed(2)} °Brix，TA ${state.ta.toFixed(2)}%，糖酸比 ${ratio.toFixed(2)}。`);
  setText("resultSummary", `口感等级 ${state.grade}，糖酸比 ${ratio.toFixed(2)}。`);
  setStepStatus("ratio", "done");
  setStepStatus("rating", "done");
  renderSystemStatus();
  if (announce) addLog(`口感分析完成：等级 ${state.grade}。`);
}

async function selectDataset() {
  try {
    setDataSource("other");
    setText("shapeStepLabel", "打开其他文件夹选择器");
    const payload = await selectFolderPath({ purpose: "sample", initial: state.analysisDataDir || state.currentCaptureDir || state.saveRootDir || "" });
    if (payload.path) {
      const inspected = await inspectImageFolders(payload.path);
      const selection = await openImageFolderSelectModal(inspected);
      if (!selection) {
        addLog("用户取消图像子目录选择，已保留原数据来源。", "WARN");
        return;
      }
      await loadSampleFolder(payload.path, {
        source: "other",
        colorDir: selection.rgbDirName,
        depthDir: selection.multispectralDirName,
        otherDirs: selection.otherImageDirs,
        strictImageDirs: true,
      });
    }
  } catch (error) {
    if (error.payload?.cancelled || String(error.message || "").includes("用户取消")) {
      addLog("用户取消选择样品文件夹，已保留原路径。", "WARN");
      return;
    }
    setStepStatus("load-rgbd", "warning");
    setText("shapeStepLabel", "样品文件夹选择失败");
    addLog(error.message || "样品文件夹选择失败。", "WARN");
  }
}

async function uploadSelectedDataset(event) {
  if (!requireActiveSample()) return;
  const files = Array.from(event.target.files || []);
  if (!files.length) {
    addLog("用户取消选择数据集。", "WARN");
    return;
  }

  const button = $("#selectDataset");
  const previousText = button?.textContent || "";
  if (button) {
    button.disabled = true;
    button.textContent = "导入中...";
  }
  setText("shapeStepLabel", "导入本地样品文件夹");
  setStepStatus("load-rgbd", "running");

  try {
    const form = new FormData();
    files.forEach((file) => {
      form.append("files", file, file.webkitRelativePath || file.name);
    });
    const response = await fetch("/api/upload-dataset", { method: "POST", body: form });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    setDataSource("other");
    await loadSampleFolder(payload.datasetDir, { source: "other" });
    setText("shapeStepLabel", "数据集已导入");
    addLog(`已导入 ${payload.fileCount} 个文件: ${payload.datasetDir}`);
  } catch (error) {
    setStepStatus("load-rgbd", "failed");
    setText("shapeStepLabel", "数据集导入失败");
    addLog(error.message || "数据集导入失败。", "ERROR");
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = previousText;
    }
  }
}

function setDataSource(source) {
  state.dataSource = source;
  if ($("#sourceCurrent")) $("#sourceCurrent").checked = source === "current";
  if ($("#sourceOther")) $("#sourceOther").checked = source !== "current";
}

function updateCurrentCaptureControls() {
  const current = $("#sourceCurrent");
  const option = $("#currentCaptureOption");
  if (current) current.disabled = !state.currentCaptureValid || !hasActiveSample();
  option?.classList.toggle("disabled", !state.currentCaptureValid);
  setText("currentCaptureHint", state.currentCaptureValid ? state.currentCaptureDir : "暂无本次拍摄数据");
}

async function handleDataSourceChange(source) {
  setDataSource(source);
  if (source === "current") {
    if (!state.currentCaptureValid || !state.currentCaptureDir) {
      const report = {
        status: "missing",
        message: "本次采集目录已不存在，请重新采集或选择其他文件夹。",
        rgbCount: 0,
        spectralCount: 0,
        pairCount: 0,
        missing: ["本次采集目录已不存在，请重新采集或选择其他文件夹。"],
      };
      renderDataCheck(report);
      updateSampleSessionFromReport(report);
      addLog("暂无可用本次拍摄数据。", "WARN");
      return;
    }
    await loadSampleFolder(state.currentCaptureDir, { source: "current" });
  } else {
    const report = {
      status: state.analysisDataDir ? "empty" : "empty",
      message: state.analysisDataDir ? "已切换为其他文件夹来源。" : "请选择其他文件夹。",
      rgbCount: 0,
      spectralCount: 0,
      pairCount: 0,
      missing: [],
    };
    renderDataCheck(report);
    updateSampleSessionFromReport(report);
  }
}

async function loadSampleFolder(datasetDir, {
  source = state.dataSource,
  colorDir = state.rgbDirName || $("#colorDir")?.value || "rgb",
  depthDir = state.multispectralDirName || $("#depthDir")?.value || "multispectral",
  otherDirs = state.otherImageDirs || [],
  strictImageDirs = false,
} = {}) {
  const target = datasetDir || "";
  state.analysisDataDir = target;
  setDataSource(source);
  setPathDisplay("#datasetDir", target);
  applyImageDirNames({
    rgbDirName: colorDir || state.rgbDirName || "rgb",
    multispectralDirName: depthDir || state.multispectralDirName || "multispectral",
    otherImageDirs: otherDirs,
  });

  if (!target) {
    state.imageBrowser.images = [];
    state.imageBrowser.index = 0;
    state.sampleSession.analysisDataDir = "";
    state.sampleSession.rgbFiles = [];
    state.sampleSession.multispectralFiles = [];
    renderDatasetImage();
    const emptyReport = {
      status: "empty",
      message: "请选择本次拍摄或其他文件夹。",
      rgbCount: 0,
      spectralCount: 0,
      pairCount: 0,
      missing: [],
    };
    renderDataCheck(emptyReport);
    updateSampleSessionFromReport(emptyReport);
    updateShapeRunButtonState();
    return;
  }

  const query = new URLSearchParams({
    datasetDir: target,
    colorDir: colorDir || "",
    depthDir: depthDir || "",
    rgbDirName: colorDir || "",
    multispectralDirName: depthDir || "",
    otherDirs: Array.isArray(otherDirs) ? otherDirs.join(",") : "",
    strictImageDirs: strictImageDirs ? "1" : "0",
    source,
  });
  const report = await api(`/api/sample-folder?${query.toString()}`);
  renderDataCheck(report);
  updateSampleSessionFromReport(report);
  if (source === "current" && report.status === "missing") {
    state.currentCaptureDir = "";
    state.currentCaptureValid = false;
    updateCurrentCaptureControls();
  }
  applyImageDirNames({
    rgbDirName: report.rgbDirName || directoryNameForInput(report.colorDir, colorDir || "rgb"),
    multispectralDirName: report.multispectralDirName || directoryNameForInput(report.depthDir, depthDir || "multispectral"),
    otherImageDirs: report.otherImageDirs || otherDirs,
  });
  if (report.valid || Number(report.rgbCount || 0) > 0) {
    await loadDatasetImages(target);
    setStepStatus("load-rgbd", report.valid ? "done" : "warning");
    setText("shapeStepLabel", report.message || "数据目录已检查");
  } else {
    state.imageBrowser.images = [];
    state.imageBrowser.index = 0;
    state.sampleSession.rgbFiles = [];
    state.sampleSession.multispectralFiles = [];
    renderDatasetImage();
    updateSampleSessionFromImages();
    setStepStatus("load-rgbd", "warning");
    setText("shapeStepLabel", report.message || "数据目录不可用");
  }
  updateShapeRunButtonState();
}

function renderDataCheck(report = {}) {
  const status = report.status || "empty";
  const complete = status === "complete";
  const card = $("#dataCheckCard");
  if (card) card.dataset.status = status;
  setText("dataCheckTitle", complete ? "✓ 数据目录有效" : status === "empty" ? "暂无数据目录" : "⚠ 数据不完整");
  setText(
    "dataCheckSummary",
    `RGB：${Number(report.rgbCount || 0)} 张 · 多光谱：${Number(report.spectralCount || 0)} 张 · 有效配对：${Number(report.pairCount || 0)} 组\n数据状态：${complete ? "完整" : (report.message || "待检查")}`
  );
  const missing = Array.isArray(report.missing) ? report.missing.filter(Boolean) : [];
  const bad = Array.isArray(report.badImages) ? report.badImages.filter(Boolean) : [];
  const lines = [];
  if (missing.length) lines.push(`缺失数据：${missing.join("；")}`);
  if (bad.length) lines.push(`无法读取：${bad.slice(0, 6).join("，")}${bad.length > 6 ? "..." : ""}`);
  setText("dataCheckMissing", lines.join("\n"));
}

async function loadDatasetImages(datasetDir = state.analysisDataDir || $("#datasetDir")?.value || "") {
  if (!datasetDir) {
    state.imageBrowser.images = [];
    state.imageBrowser.index = 0;
    renderDatasetImage();
    return;
  }
  const query = new URLSearchParams({
    datasetDir,
    colorDir: state.rgbDirName || $("#colorDir")?.value || "",
    depthDir: state.multispectralDirName || $("#depthDir")?.value || "",
    rgbDirName: state.rgbDirName || $("#colorDir")?.value || "",
    multispectralDirName: state.multispectralDirName || $("#depthDir")?.value || "",
  });
  try {
    const payload = await api(`/api/dataset-images?${query.toString()}`);
    state.imageBrowser.images = payload.images || [];
    state.imageBrowser.index = 0;
    applyImageDirNames({
      rgbDirName: payload.rgbDirName || directoryNameForInput(payload.colorDir, state.rgbDirName || "rgb"),
      multispectralDirName: payload.multispectralDirName || directoryNameForInput(payload.depthDir, state.multispectralDirName || "multispectral"),
      otherImageDirs: state.otherImageDirs,
    });
    renderDatasetImage();
    updateSampleSessionFromImages();
    setStepStatus("image-review", state.imageBrowser.images.length ? "done" : "warning");
    addLog(`图片浏览已加载 ${state.imageBrowser.images.length} 组样品图片。`);
  } catch (error) {
    state.imageBrowser.images = [];
    state.imageBrowser.index = 0;
    renderDatasetImage();
    updateSampleSessionFromImages();
    setStepStatus("image-review", "warning");
    addLog(`图片浏览未加载: ${error.message}`, "WARN");
  }
}

function renderDatasetImage() {
  const images = state.imageBrowser.images;
  const color = $("#colorPreview");
  const depth = $("#depthPreview");
  if (!images.length) {
    setPreviewImage("#colorPreview", "#colorPreviewEmpty");
    setPreviewImage("#depthPreview", "#depthPreviewEmpty");
    setText("imageBrowserCount", "暂无图片");
    setText("colorPreviewName", "彩色图");
    setText("depthPreviewName", "多光谱图");
    return;
  }
  const index = Math.max(0, Math.min(state.imageBrowser.index, images.length - 1));
  state.imageBrowser.index = index;
  const item = images[index];
  setPreviewImage("#colorPreview", "#colorPreviewEmpty", `${item.color.url}&t=${Date.now()}`);
  setPreviewImage("#depthPreview", "#depthPreviewEmpty", item.depth?.url ? `${item.depth.url}&t=${Date.now()}` : "");
  setText("imageBrowserCount", `${index + 1} / ${images.length}`);
  setText("colorPreviewName", item.color.name || "彩色图");
  setText("depthPreviewName", item.depth?.name || "未提供多光谱图");
}

function stepDatasetImage(delta) {
  if (!state.imageBrowser.images.length) return;
  const count = state.imageBrowser.images.length;
  state.imageBrowser.index = (state.imageBrowser.index + delta + count) % count;
  renderDatasetImage();
}

async function runShapeAnalysis() {
  if (!state.analysisDataDir && !$("#datasetDir")?.value) {
    setText("shapeStepLabel", "请先选择样品文件夹");
    setStepStatus("load-rgbd", "warning");
    addLog("请先选择样品文件夹并确认数据目录有效。", "WARN");
    return;
  }
  if (state.shapeJobId) {
    addLog("已有形态分析任务正在运行。", "WARN");
    return;
  }
  const mode = $("#shapeMode")?.value || "morphology2d";
  if (mode === "pointcloud3d") {
    setText("shapeStepLabel", "三维点云建模为后续预留");
    setStepStatus("volume", "warning");
    addLog("点云建模需要深度来源、多角度标定重建或外部点云模型；当前两相机流程先执行普通形态测算。", "WARN");
    return;
  }
  const button = $("#runShapeAnalysis");
  const cancel = $("#cancelShapeAnalysis");
  button.disabled = true;
  cancel.disabled = false;
  state.shapeStartedAt = performance.now();
  state.systemTask = "shape";
  resetShapeStatus();
  renderSystemStatus();
  setStepStatus("load-rgbd", "running");
  setCurrentStep("load-rgbd");
  setText("shapeStepLabel", "提交任务中");
  $("#shapeProgress").style.width = "0%";

  try {
    const camera = collectCameraSettingsFromForm();
    const payload = await api("/api/analyze-shape", {
      method: "POST",
      body: JSON.stringify({
        datasetDir: state.analysisDataDir || $("#datasetDir")?.value || "",
        colorDir: state.rgbDirName || $("#colorDir")?.value || "",
        depthDir: state.multispectralDirName || $("#depthDir")?.value || "",
        rgbDirName: state.rgbDirName || $("#colorDir")?.value || "",
        multispectralDirName: state.multispectralDirName || $("#depthDir")?.value || "",
        fx: camera.fx,
        fy: camera.fy,
        cx: camera.cx,
        cy: camera.cy,
        densityGCm3: 1.08,
        voxelSizeMm: 2.0,
        maxPairs: 10,
      }),
    });
    state.shapeJobId = payload.jobId;
    addLog(`形态分析任务已启动: ${state.shapeJobId}`);
    state.shapeTimer = window.setInterval(pollShapeJob, 650);
    await pollShapeJob();
  } catch (error) {
    finishShapeJob();
    setStepStatus("load-rgbd", "failed");
    setText("shapeStepLabel", "任务启动失败");
    addLog(error.message, "ERROR");
  }
}

async function pollShapeJob() {
  if (!state.shapeJobId) return;
  try {
    const payload = await api(`/api/jobs/${state.shapeJobId}`);
    const job = payload.job;
    renderShapeJob(job);
    if (["done", "failed", "cancelled"].includes(job.status)) {
      finishShapeJob();
      if (job.status === "done") {
        renderShapeResult(job.result);
      } else {
        const message = job.error?.message || job.message || "分析失败";
        addLog(message, job.status === "cancelled" ? "WARN" : "ERROR");
      }
    }
  } catch (error) {
    finishShapeJob();
    addLog(`查询任务失败: ${error.message}`, "ERROR");
  }
}

function renderShapeJob(job) {
  const progress = Number(job.progress || 0);
  $("#shapeProgress").style.width = `${progress}%`;
  setText("shapeStepLabel", job.message || job.step || "运行中");
  if (state.shapeStartedAt) {
    setText("shapeElapsed", `${((performance.now() - state.shapeStartedAt) / 1000).toFixed(1)}s`);
  }
  const key = shapeStepMap[job.step] || "load-rgbd";
  setCurrentStep(key);
  setStepStatus(key, job.status === "failed" ? "failed" : "running");
  markCompletedShapeSteps(key);
  const logs = job.logs || [];
  if (logs.length) {
    const last = logs[logs.length - 1];
    if (!$("#runLog").textContent.includes(last)) addLog(last.replace(/^\[[^\]]+\]\s*/, ""));
  }
}

function markCompletedShapeSteps(currentKey) {
  const order = ["load-rgbd", "preprocess", "image-review", "filter", "surface-texture", "measure", "volume", "confirm"];
  const currentIndex = order.indexOf(currentKey);
  order.forEach((key, index) => {
    if (index < currentIndex) setStepStatus(key, "done");
  });
}

function renderShapeResult(result) {
  if (!result) return;
  const detail = result.details?.[0] || {};
  const hasPointcloud = Number(result.pointCount || 0) > 0;
  const formatLength = (mmValue, pxValue) => {
    const mm = Number(mmValue);
    const px = Number(pxValue);
    const hasMm = Number.isFinite(mm) && mm > 0;
    const hasPx = Number.isFinite(px) && px > 0;
    if (hasMm && hasPx) return `${mm.toFixed(2)} mm（${px.toFixed(2)} px）`;
    if (hasMm) return `${mm.toFixed(2)} mm`;
    if (hasPx) return `${px.toFixed(2)} px`;
    return "--";
  };
  setText("metricDepth", detail.areaPixels ? `${detail.areaPixels} px` : "--");
  setText("metricDiameter", formatLength(result.diameterMm, result.diameterPx ?? detail.diameterPx));
  setText("metricHeight", formatLength(result.heightMm, result.heightPx ?? detail.heightPx));
  setText("metricVolume", hasPointcloud ? `${Number(result.volumeMm3).toFixed(2)} mm³` : "待三维方案");
  setText("metricWeight", hasPointcloud ? `${Number(result.weightG).toFixed(2)} g` : "待三维方案");
  renderTextureResult(result.texture);
  setText("resultShape", hasPointcloud ? `二维形态 + 点云数值 ${result.pointCount} 点` : "二维形态与表面分析完成");
  setText("resultSummary", `形态分析成功，用时 ${result.elapsedSec}s。`);
  setStepStatus("confirm", "done");
  state.shapeDone = true;
  renderSystemStatus();
  if (result.inputPreviewUrl) setPreviewImage("#colorPreview", "#colorPreviewEmpty", `${result.inputPreviewUrl}?t=${Date.now()}`);
  if (result.plyUrl) loadPointcloudViewer(result.plyUrl);
  addLog(hasPointcloud ? `形态分析成功：已读取点云模型 ${result.pointCount} 点。` : "形态分析成功：已完成 RGB 图像形态与表面分析。");
}

function resetShapeStatus() {
  clearPointcloudViewer();
  resetTextureResult();
  state.shapeDone = false;
  ["load-rgbd", "preprocess", "image-review", "filter", "surface-texture", "measure", "volume", "confirm"].forEach((key) => {
    setStepStatus(key, "waiting");
  });
}

function renderTextureResult(texture) {
  if (!texture || !texture.ok) {
    const message = texture?.message || "未获得 RGB 图片";
    setText("textureStatus", message);
  setText("metricBloom", "--");
  setText("metricBloomSide", "--");
  setText("metricUniformity", "--");
  setPreviewImage("#texturePreview", "#texturePreviewEmpty");
  setStepStatus("surface-texture", "warning");
  return;
  }
  const bloom = `${Number(texture.bloomCoveragePercent).toFixed(2)}%`;
  setText("textureStatus", texture.message || "分析完成");
  setText("metricBloom", bloom);
  setText("metricBloomSide", bloom);
  setText("metricUniformity", `${Number(texture.colorUniformity).toFixed(1)}`);
  setPreviewImage("#texturePreview", "#texturePreviewEmpty", texture.previewUrl ? `${texture.previewUrl}?t=${Date.now()}` : "");
  setStepStatus("surface-texture", "done");
}

function resetTextureResult() {
  setText("textureStatus", "等待分析");
  setText("metricBloom", "--");
  setText("metricBloomSide", "--");
  setText("metricUniformity", "--");
  setPreviewImage("#texturePreview", "#texturePreviewEmpty");
}

function updateShapeMode(mode = $("#shapeMode")?.value || "morphology2d") {
  state.shapeMode = mode;
  const isPointcloud = mode === "pointcloud3d";
  $("#pointcloudSection")?.classList.toggle("is-hidden", !isPointcloud);
  if (isPointcloud) resizePointcloudCanvas();
  setText(
    "shapeModeExplain",
    isPointcloud
      ? "该入口用于后续多角度重建、深度相机或外部点云文件接入；当前硬件未提供深度信息，暂不直接生成三维模型。"
      : "使用彩色图像提取样品轮廓、面积、水平宽度、垂直高度、颜色均匀度、果粉与纹理特征。"
  );
  updateShapeRunButtonState();
  if (isPointcloud) {
    setText("shapeStepLabel", "三维建模入口已预留");
    setStepStatus("volume", "warning");
  } else {
    setText("shapeStepLabel", "未开始");
    setStepStatus("volume", "waiting");
  }
}

async function cancelShapeAnalysis() {
  if (!state.shapeJobId) return;
  try {
    await api(`/api/jobs/${state.shapeJobId}/cancel`, { method: "POST", body: "{}" });
    addLog("已请求取消形态分析任务。", "WARN");
  } catch (error) {
    addLog(`取消失败: ${error.message}`, "ERROR");
  }
}

function finishShapeJob() {
  if (state.shapeTimer) window.clearInterval(state.shapeTimer);
  state.shapeTimer = null;
  state.shapeJobId = null;
  if (state.systemTask === "shape") state.systemTask = "";
  updateShapeRunButtonState();
  if ($("#cancelShapeAnalysis")) $("#cancelShapeAnalysis").disabled = true;
  updateShapeMode();
  renderSystemStatus();
}

function exportReport() {
  const lines = [
    "果实口感多光谱无损检测系统 - 检测报告",
    `样品编号: ${$("#sampleId")?.value || "--"}`,
    `糖度 SSC: ${$("#resultSsc")?.textContent || "--"}`,
    `酸度 TA: ${$("#resultTa")?.textContent || "--"}`,
    `pH: ${$("#resultPh")?.textContent || "--"}`,
    `糖酸比: ${$("#resultRatio")?.textContent || "--"}`,
    `综合等级: ${$("#gradeValue")?.textContent || "--"}`,
    `形态分析: ${$("#resultShape")?.textContent || "--"}`,
    `果粉覆盖率: ${$("#metricBloomSide")?.textContent || "--"}`,
    "说明: 硬件控制仍为预留；形态分析在本地 Python 后端执行。",
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "fruit_quality_report.txt";
  link.click();
  URL.revokeObjectURL(link.href);
  setStepStatus("export", "done");
  addLog("报告已导出为文本文件。");
}

function updateClock() {
  setText("currentTime", new Date().toLocaleTimeString("zh-CN", { hour12: false }));
}

function openModelStudio() {
  const url = `${window.location.origin}/model-studio`;
  window.open(url, "_blank", "noopener");
  addLog("已打开模型训练与数据管理平台。");
}

async function shutdownApp() {
  try {
    await api("/api/shutdown", { method: "POST", body: "{}" });
  } catch {
    addLog("已请求退出程序。", "WARN");
  }
  window.close();
}

document.addEventListener("DOMContentLoaded", async () => {
  initPointcloudViewer();
  applyModuleLayout(document.querySelector(".view-page.active")?.dataset.page || "motor");

  document.querySelectorAll(".task-step").forEach((button) => {
    button.dataset.status = button.dataset.status || "idle";
    button.addEventListener("click", () => switchView(button.dataset.view, button.dataset.stepKey));
  });

  document.querySelectorAll("[data-test]").forEach((button) => {
    button.addEventListener("click", () => runDeviceTest(button.dataset.test).catch((error) => addLog(error.message, "WARN")));
  });

  document.querySelectorAll("[data-log]").forEach((button) => {
    button.addEventListener("click", () => addLog(button.dataset.log));
  });

  document.querySelectorAll("[data-step]").forEach((button) => {
    button.addEventListener("click", () => updateCaptureProgress(Number(button.dataset.step)));
  });

  document.querySelectorAll(".lamp").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".lamp").forEach((lamp) => lamp.classList.remove("active"));
      button.classList.add("active");
      addLog(`当前光源波段切换为 ${button.dataset.band}nm（离线模拟）。`);
    });
  });

  $("#sampleId")?.addEventListener("input", () => {
    setText("resultSampleName", $("#sampleId").value || "--");
    state.sampleSession.sampleId = $("#sampleId").value || "--";
    renderQualitySampleSummary();
  });

  ["#qualityFruitType", "#qualityVariety"].forEach((selector) => {
    $(selector)?.addEventListener("change", async () => {
      await loadSampleTypeCatalog().catch((error) => addLog(error.message, "WARN"));
      state.fruitType = $("#qualityFruitType")?.value || state.fruitType || "";
      state.variety = $("#qualityVariety")?.value || state.variety || "generic";
      await loadQualityModels().catch((error) => addLog(error.message, "WARN"));
    });
  });
  $("#toggleModelAdvanced")?.addEventListener("click", toggleModelAdvanced);
  $("#sscModelSelect")?.addEventListener("change", () => {
    if (hasActiveSample() && Number.isFinite(state.ssc)) {
      const ok = window.confirm("更换糖度模型将清空当前 SSC 预测结果和口感结果。");
      if (!ok) {
        $("#sscModelSelect").value = state.selectedSscModelId || "";
        return;
      }
      clearSscPrediction();
    }
    state.selectedSscModelId = $("#sscModelSelect")?.value || "";
    if (hasActiveSample()) saveModelSelection().catch((error) => addLog(error.message, "WARN"));
    updateAnalysisButtonStates();
    renderModelOverview();
  });
  ["#taModelSelect", "#phModelSelect"].forEach((selector) => {
    $(selector)?.addEventListener("change", () => {
      if (hasActiveSample() && (Number.isFinite(state.ta) || Number.isFinite(state.ph))) {
        const ok = window.confirm("更换酸度或 pH 模型将清空当前 TA / pH 预测结果和口感结果。");
        if (!ok) {
          if ($("#taModelSelect")) $("#taModelSelect").value = state.selectedTaModelId || "";
          if ($("#phModelSelect")) $("#phModelSelect").value = state.selectedPhModelId || "";
          return;
        }
        clearAcidPrediction();
      }
      state.selectedTaModelId = $("#taModelSelect")?.value || "";
      state.selectedPhModelId = $("#phModelSelect")?.value || "";
      if (hasActiveSample()) saveModelSelection().catch((error) => addLog(error.message, "WARN"));
      updateAnalysisButtonStates();
      renderModelOverview();
    });
  });
  $("#createSampleInline")?.addEventListener("click", () => createNewSample().catch((error) => {
    setText("sampleCreateStatus", "创建失败");
    addLog(error.message || "创建样品失败。", "ERROR");
  }));
  $("#chooseSaveRoot")?.addEventListener("click", chooseSaveRoot);
  $("#openCaptureFolder")?.addEventListener("click", openCaptureFolder);
  $("#closeSampleModal")?.addEventListener("click", closeSampleModal);
  $("#cancelNewSample")?.addEventListener("click", closeSampleModal);
  $("#createNewSample")?.addEventListener("click", () => createNewSample().catch((error) => setText("newSampleHint", error.message)));
  $("#closeImageDirSettingsModal")?.addEventListener("click", () => closeImageDirSettingsModal(null));
  $("#cancelImageDirSettings")?.addEventListener("click", () => closeImageDirSettingsModal(null));
  $("#confirmImageDirSettings")?.addEventListener("click", confirmImageDirSettingsModal);
  $("#closeImageFolderSelectModal")?.addEventListener("click", () => closeImageFolderSelectModal(null));
  $("#cancelImageFolderSelect")?.addEventListener("click", () => closeImageFolderSelectModal(null));
  $("#confirmImageFolderSelect")?.addEventListener("click", confirmImageFolderSelectModal);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("#imageFolderSelectModal")?.hidden) closeImageFolderSelectModal(null);
    else if (!$("#imageDirSettingsModal")?.hidden) closeImageDirSettingsModal(null);
    else if (!$("#sampleModal")?.hidden) closeSampleModal();
  });
  $("#newSampleFruitType")?.addEventListener("change", () => loadNewSampleCatalog().catch((error) => setText("newSampleHint", error.message)));
  $("#newSampleVariety")?.addEventListener("change", () => loadNewSampleCatalog().catch((error) => setText("newSampleHint", error.message)));
  [
    "#multiViewEnabled",
    "#rotationIntervalDeg",
    "#rotationStartAngleDeg",
    "#rotationDirection",
    "#includeClosureView",
  ].forEach((selector) => {
    $(selector)?.addEventListener("input", () => renderRotationPlan());
    $(selector)?.addEventListener("change", () => renderRotationPlan());
  });
  document.querySelectorAll("[data-rotation-interval]").forEach((button) => {
    button.addEventListener("click", () => {
      if ($("#rotationIntervalDeg")) $("#rotationIntervalDeg").value = button.dataset.rotationInterval || DEFAULT_ROTATION_SETTINGS.expectedIntervalDeg;
      renderRotationPlan();
    });
  });

  $("#refreshPorts")?.addEventListener("click", () => loadDevicePorts());
  $("#connectDevice")?.addEventListener("click", () => connectDevice());
  $("#disconnectDevice")?.addEventListener("click", () => disconnectDevice());
  $("#faultClearDevice")?.addEventListener("click", () => faultClearDevice());
  $("#refreshDeviceStatus")?.addEventListener("click", () => refreshHardwareStatus());
  $("#startDeviceCheck")?.addEventListener("click", () => runUnifiedDeviceCheck());
  $("#hardwareSelfTest")?.addEventListener("click", () => runHardwareSelfTest(false));
  $("#hardwareMotionSelfTest")?.addEventListener("click", () => runHardwareSelfTest(true));

  $("#startWorkflow")?.addEventListener("click", () => {
    if (!requireDevicePreparation()) return;
    if (!requireActiveSample()) return;
    switchView("capture", "sample");
    setStepStatus("sample", "running");
    addLog("检测流程已启动：按离线模式进入样品采集。");
  });

  $("#emergencyStop")?.addEventListener("click", () => emergencyStopDevice());

  $("#startSscAnalysis")?.addEventListener("click", runSscAnalysis);
  $("#startAcidAnalysis")?.addEventListener("click", runAcidAnalysis);
  $("#evaluateTaste")?.addEventListener("click", () => updateTaste(true));
  document.querySelectorAll("[data-camera-settings-tab]").forEach((button) => {
    button.addEventListener("click", () => setCameraSettingsTab(button.dataset.cameraSettingsTab));
  });
  ["#cameraAutoExposureEnabled", "#cameraGainAuto", "#cameraAutoWhiteBalanceEnabled"].forEach((selector) => {
    $(selector)?.addEventListener("change", updateCameraParameterControlState);
  });
  $("#testRgbCamera")?.addEventListener("click", probeRgbCamera);
  $("#applyRgbCameraSettings")?.addEventListener("click", applyRgbCameraSettings);
  $("#startRgbPreview")?.addEventListener("click", startRgbPreview);
  $("#stopRgbPreview")?.addEventListener("click", stopRgbPreview);
  $("#testMultispectralCamera")?.addEventListener("click", probeMultispectralCamera);
  $("#applyMultispectralCameraSettings")?.addEventListener("click", applyMultispectralCameraSettings);
  $("#startMultispectralPreview")?.addEventListener("click", startMultispectralPreview);
  $("#stopMultispectralPreview")?.addEventListener("click", stopMultispectralPreview);
  $("#saveCameraSettings")?.addEventListener("click", saveCameraSettings);
  $("#resetCameraSettings")?.addEventListener("click", resetCameraSettings);
  $("#confirmCalibration")?.addEventListener("click", () => confirmCalibrationCheck().catch((error) => addLog(error.message, "WARN")));
  $("#shapeMode")?.addEventListener("change", (event) => updateShapeMode(event.target.value));
  document.querySelectorAll('input[name="dataSource"]').forEach((input) => {
    input.addEventListener("change", (event) => handleDataSourceChange(event.target.value));
  });
  $("#enterAnalysisFromCapture")?.addEventListener("click", enterAnalysisFromCapture);
  $("#selectDataset")?.addEventListener("click", selectDataset);
  $("#prevImage")?.addEventListener("click", () => stepDatasetImage(-1));
  $("#nextImage")?.addEventListener("click", () => stepDatasetImage(1));
  $("#refreshImages")?.addEventListener("click", () => loadSampleFolder(state.analysisDataDir || $("#datasetDir")?.value || "", { source: state.dataSource }));
  $("#runShapeAnalysis")?.addEventListener("click", runShapeAnalysis);
  $("#cancelShapeAnalysis")?.addEventListener("click", cancelShapeAnalysis);
  $("#exportReport")?.addEventListener("click", exportReport);
  $("#clearLog")?.addEventListener("click", () => {
    setText("runLog", "[INFO] 日志已清空。");
  });
  $("#saveLog")?.addEventListener("click", exportReport);

  $("#fullscreenButton")?.addEventListener("click", () => {
    document.documentElement.requestFullscreen?.();
  });

  $("#helpButton")?.addEventListener("click", () => {
    addLog("帮助：左侧任务树按设备准备、采集、形态、糖酸、报告和设置组织；形态分析会调用本地 Python 后端。");
  });

  $("#modelStudioButton")?.addEventListener("click", openModelStudio);
  $("#exitButton")?.addEventListener("click", shutdownApp);

  window.setInterval(updateClock, 1000);
  updateClock();
  applyCameraSettings();
  setCameraSettingsTab("rgb");
  renderRgbApplySummary();
  renderRotationPlan();
  resetCaptureStepStatuses();
  renderCalibrationStatus();
  updateDevicePreparationControls();
  updateShapeMode();
  renderDeviceChecks(checksFromHardwareStatus());
  renderSystemStatus();
  await loadDevicePorts();
  await refreshHardwareStatus();
  try {
    const status = await api("/api/status");
    applyHardwareStatus(status.device || {});
    applyCameraStatus(status.cameras || status.device?.cameras || {});
    if (status.devicePrep) state.devicePrep = { ...state.devicePrep, ...status.devicePrep };
    if (state.hardwareStatus.connected) state.devicePrep.connect = true;
    renderDevicePreparationStatus();
    applySampleSessionState(status);
    state.saveRootDir = status.saveRootDir || state.saveRootDir || "";
    state.captureStarted = Boolean(status.captureStarted);
    state.currentCaptureDir = status.currentCaptureDir || "";
    state.currentCaptureValid = Boolean(status.currentCaptureValid && state.currentCaptureDir);
    state.analysisDataDir = status.analysisDataDir || status.sampleDataset || "";
    applyImageDirNames({
      rgbDirName: status.rgbDirName || status.colorDir || "rgb",
      multispectralDirName: status.multispectralDirName || status.depthDir || "multispectral",
      otherImageDirs: status.otherImageDirs || [],
    });
    await loadSampleTypeCatalog().catch((error) => addLog(error.message, "WARN"));
    if ($("#qualityFruitType") && state.fruitType) $("#qualityFruitType").value = state.fruitType;
    if ($("#qualityVariety") && state.variety) $("#qualityVariety").value = state.variety;
    await loadQualityModels().catch((error) => addLog(error.message, "WARN"));
    renderCurrentSample();
    updateCurrentCaptureControls();
    if (hasActiveSample() && state.analysisDataDir) {
      const source = state.currentCaptureValid && state.analysisDataDir === state.currentCaptureDir ? "current" : "other";
      await loadSampleFolder(state.analysisDataDir, { source });
    } else if (hasActiveSample() && state.currentCaptureValid) {
      await loadSampleFolder(state.currentCaptureDir, { source: "current" });
    } else {
      setDataSource("other");
      const report = {
        status: "empty",
        message: "请选择本次拍摄或其他文件夹。",
        rgbCount: 0,
        spectralCount: 0,
        pairCount: 0,
        missing: [],
      };
      renderDataCheck(report);
      updateSampleSessionFromReport(report);
    }
    if (status.dependencies?.PIL && status.dependencies?.numpy) {
      addLog("Python 后端已连接，图像分析依赖可用。");
    }
    if (!status.dependencies?.cv2) {
      addLog("未检测到 OpenCV：可选点云模型读取和部分图像处理可能受限。", "WARN");
    }
    await loadDevicePorts();
  } catch (error) {
    addLog(`后端未连接: ${error.message}`, "ERROR");
  }
});
