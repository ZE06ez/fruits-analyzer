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
  shapeMode: "morphology2d",
  saveRootDir: "",
  currentCaptureDir: "",
  currentCaptureValid: false,
  analysisDataDir: "",
  captureStarted: false,
  calibrationStatus: "pending",
  devicePrep: {
    connect: false,
    motor: false,
    light: false,
    camera: false,
    calibration: false,
  },
  dataSource: "other",
  captureCompleting: false,
  hasSample: false,
  sampleName: "",
  sampleId: "",
  sampleCreatedAt: "",
  fruitType: "",
  variety: "generic",
  selectedSscModelId: "",
  selectedTaModelId: "",
  selectedPhModelId: "",
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
  resolution: "1280 x 720",
  exposure: "自动",
  fx: 652.77,
  fy: 652.77,
  cx: 631.75,
  cy: 364.95,
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

function $(selector) {
  return document.querySelector(selector);
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
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

function readCameraSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(CAMERA_SETTINGS_KEY) || "{}");
    return {
      resolution: String(saved.resolution || DEFAULT_CAMERA_SETTINGS.resolution),
      exposure: String(saved.exposure || DEFAULT_CAMERA_SETTINGS.exposure),
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
  return {
    resolution: $("#cameraResolution")?.value.trim() || DEFAULT_CAMERA_SETTINGS.resolution,
    exposure: $("#cameraExposure")?.value.trim() || DEFAULT_CAMERA_SETTINGS.exposure,
    fx: parseNumberSetting($("#cameraFx")?.value, DEFAULT_CAMERA_SETTINGS.fx),
    fy: parseNumberSetting($("#cameraFy")?.value, DEFAULT_CAMERA_SETTINGS.fy),
    cx: parseNumberSetting($("#cameraCx")?.value, DEFAULT_CAMERA_SETTINGS.cx),
    cy: parseNumberSetting($("#cameraCy")?.value, DEFAULT_CAMERA_SETTINGS.cy),
  };
}

function applyCameraSettings(settings = readCameraSettings()) {
  if ($("#cameraResolution")) $("#cameraResolution").value = settings.resolution;
  if ($("#cameraExposure")) $("#cameraExposure").value = settings.exposure;
  if ($("#cameraFx")) $("#cameraFx").value = settings.fx;
  if ($("#cameraFy")) $("#cameraFy").value = settings.fy;
  if ($("#cameraCx")) $("#cameraCx").value = settings.cx;
  if ($("#cameraCy")) $("#cameraCy").value = settings.cy;
  setText("cameraSettingsStatus", "当前参数");
}

function saveCameraSettings() {
  const settings = collectCameraSettingsFromForm();
  localStorage.setItem(CAMERA_SETTINGS_KEY, JSON.stringify(settings));
  applyCameraSettings(settings);
  setText("cameraSettingsStatus", "已保存");
  addLog(`相机参数已保存：fx/fy=${settings.fx}/${settings.fy}，cx/cy=${settings.cx}/${settings.cy}`);
}

function resetCameraSettings() {
  localStorage.removeItem(CAMERA_SETTINGS_KEY);
  applyCameraSettings({ ...DEFAULT_CAMERA_SETTINGS });
  setText("cameraSettingsStatus", "已恢复默认");
  addLog("相机参数已恢复为默认值。", "WARN");
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

function isDevicePreparationReady() {
  return Boolean(
    state.devicePrep.connect
    && state.devicePrep.motor
    && state.devicePrep.light
    && state.devicePrep.camera
    && state.devicePrep.calibration
  );
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
  const hint = ready ? "" : "请先完成设备准备：连接检查、电机、光源、相机和标定检查。";
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
  if (ready) setText("statusNote", "设备准备已完成，可以开始样品采集。");
}

function renderDevicePreparationStatus() {
  if (state.devicePrep.connect) {
    setPill("serialStatus", "串口: 离线连接检查通过", "ok");
    setStepStatus("connect", "done");
  }
  if (state.devicePrep.motor) {
    setPill("motorStatus", "电机: 离线自检通过", "ok");
    setStepStatus("motor", "done");
  }
  if (state.devicePrep.light) {
    setPill("lightStatus", "光源: 离线自检通过", "ok");
    setStepStatus("light", "done");
  }
  if (state.devicePrep.camera) {
    setPill("cameraStatus", "相机: 离线自检通过", "ok");
    setStepStatus("camera", "done");
  }
  if (state.devicePrep.calibration) {
    state.calibrationStatus = "passed";
    renderCalibrationStatus();
  }
  updateDevicePreparationControls();
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
  updateDevicePreparationControls();
  updateAnalysisButtonStates();
  updateShapeMode();
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
  updateCurrentCaptureControls();
  resetCaptureStepStatuses();
  renderCalibrationStatus();
}

function addLog(message, level = "INFO") {
  const log = $("#runLog");
  if (!log) return;
  const stamp = new Date().toTimeString().slice(0, 8);
  log.textContent += `\n[${stamp}] [${level}] ${message}`;
  log.scrollTop = log.scrollHeight;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    const error = new Error(payload.error || payload.message || `HTTP ${response.status}`);
    error.payload = payload;
    throw error;
  }
  return payload;
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
      text: "相机: 离线自检通过",
      step: "camera",
      log: "相机检测完成：彩色相机与多光谱相机为模拟通过状态。",
    },
  };
  const item = map[type];
  if (!item) return;
  setPill(item.pill, item.text, "ok");
  setStepStatus(item.step, "done");
  state.devicePrep[type] = true;
  setText("statusNote", "硬件通信尚未接入，当前自检结果来自离线模拟。");
  addLog(item.log);
  await syncDevicePreparation();
}

function resetCaptureStepStatuses() {
  ["sample", "dark", "white", "rgb", "spectral", "integrity"].forEach((key) => setStepStatus(key, "waiting"));
  if ($("#captureProgress")) $("#captureProgress").style.width = "0%";
  setText("captureProgressText", "采集进度: 0 / 4");
  setText("captureSaveStatus", "等待采集完成");
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
  state.captureStep = Math.max(state.captureStep, step);
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
  const button = $("#enterAnalysisFromCapture");
  try {
    setText("captureSaveStatus", "正在保存本次拍摄数据...");
    const payload = await api("/api/complete-capture", {
      method: "POST",
      body: JSON.stringify({ sampleId: $("#sampleId")?.value || "" }),
    });
    state.currentCaptureDir = payload.currentCaptureDir || "";
    state.currentCaptureValid = Boolean(state.currentCaptureDir);
    state.analysisDataDir = payload.analysisDataDir || state.currentCaptureDir;
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
    colorDir: $("#colorDir")?.value || "rgb",
    depthDir: $("#depthDir")?.value || "multispectral",
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
  setText("sampleCreateStatus", "正在创建样品");
  await loadSampleTypeCatalog().catch((error) => addLog(error.message, "WARN"));
  const payload = {
    sampleName,
    saveRootDir,
    fruitType: $("#qualityFruitType")?.value || $("#newSampleFruitType")?.value || "",
    variety: $("#qualityVariety")?.value || $("#newSampleVariety")?.value || "generic",
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
  state.captureStarted = Boolean(sample.captureStarted);
  state.fruitType = sample.fruitType || "";
  state.variety = sample.variety || "generic";
  state.selectedSscModelId = sample.selectedSscModelId || "";
  state.selectedTaModelId = sample.selectedTaModelId || "";
  state.selectedPhModelId = sample.selectedPhModelId || "";
  state.sampleSession.sampleId = state.sampleId;
  state.sampleSession.sampleName = state.sampleName;
  state.sampleSession.fruitType = state.fruitType;
  state.sampleSession.variety = state.variety;
  if ($("#qualityFruitType")) $("#qualityFruitType").value = state.fruitType;
  if ($("#qualityVariety")) $("#qualityVariety").value = state.variety;
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
      state.saveRootDir = selected;
      setPathDisplay("#saveRootDir", selected);
      setText("sampleCreateStatus", "保存位置已选择");
      addLog(`样品保存位置已选择: ${selected}`);
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
    updateAnalysisButtonStates();
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
    updateAnalysisButtonStates();
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
  if (announce) addLog(`口感分析完成：等级 ${state.grade}。`);
}

async function selectDataset() {
  try {
    setDataSource("other");
    setText("shapeStepLabel", "打开其他文件夹选择器");
    const payload = await selectFolderPath({ purpose: "sample", initial: state.analysisDataDir || state.currentCaptureDir || state.saveRootDir || "" });
    if (payload.path) {
      await loadSampleFolder(payload.path, { source: "other" });
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

async function loadSampleFolder(datasetDir, { source = state.dataSource } = {}) {
  const target = datasetDir || "";
  state.analysisDataDir = target;
  setDataSource(source);
  setPathDisplay("#datasetDir", target);
  $("#colorDir").value = $("#colorDir").value || "rgb";
  $("#depthDir").value = $("#depthDir").value || "multispectral";

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
    colorDir: $("#colorDir")?.value || "",
    depthDir: $("#depthDir")?.value || "",
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
  if (report.colorDir) $("#colorDir").value = directoryNameForInput(report.colorDir, $("#colorDir")?.value || "rgb");
  if (report.depthDir) $("#depthDir").value = directoryNameForInput(report.depthDir, $("#depthDir")?.value || "multispectral");
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
    colorDir: $("#colorDir")?.value || "",
    depthDir: $("#depthDir")?.value || "",
  });
  try {
    const payload = await api(`/api/dataset-images?${query.toString()}`);
    state.imageBrowser.images = payload.images || [];
    state.imageBrowser.index = 0;
    if (payload.colorDir) $("#colorDir").value = directoryNameForInput(payload.colorDir, $("#colorDir")?.value || "rgb");
    if (payload.depthDir) $("#depthDir").value = directoryNameForInput(payload.depthDir, $("#depthDir")?.value || "multispectral");
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
  resetShapeStatus();
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
        colorDir: $("#colorDir")?.value || "",
        depthDir: $("#depthDir")?.value || "",
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
  setText("metricDepth", detail.areaPixels ? `${detail.areaPixels} px` : "--");
  setText("metricDiameter", detail.diameterPx ? `${Number(detail.diameterPx).toFixed(2)} px` : `${Number(result.diameterMm || 0).toFixed(2)} mm`);
  setText("metricHeight", detail.heightPx ? `${Number(detail.heightPx).toFixed(2)} px` : `${Number(result.heightMm || 0).toFixed(2)} mm`);
  setText("metricVolume", hasPointcloud ? `${Number(result.volumeMm3).toFixed(2)} mm³` : "待三维方案");
  setText("metricWeight", hasPointcloud ? `${Number(result.weightG).toFixed(2)} g` : "待三维方案");
  renderTextureResult(result.texture);
  setText("resultShape", hasPointcloud ? `二维形态 + 点云数值 ${result.pointCount} 点` : "二维形态与表面分析完成");
  setText("resultSummary", `形态分析成功，用时 ${result.elapsedSec}s。`);
  setStepStatus("confirm", "done");
  if (result.inputPreviewUrl) setPreviewImage("#colorPreview", "#colorPreviewEmpty", `${result.inputPreviewUrl}?t=${Date.now()}`);
  if (result.plyUrl) loadPointcloudViewer(result.plyUrl);
  addLog(hasPointcloud ? `形态分析成功：已读取点云模型 ${result.pointCount} 点。` : "形态分析成功：已完成 RGB 图像形态与表面分析。");
}

function resetShapeStatus() {
  clearPointcloudViewer();
  resetTextureResult();
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
  updateShapeRunButtonState();
  if ($("#cancelShapeAnalysis")) $("#cancelShapeAnalysis").disabled = true;
  updateShapeMode();
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
    $(selector)?.addEventListener("change", () => {
      loadSampleTypeCatalog().catch((error) => addLog(error.message, "WARN"));
    });
  });
  $("#sscModelSelect")?.addEventListener("change", () => {
    if (hasActiveSample() && Number.isFinite(state.ssc)) {
      const ok = window.confirm("更换糖度模型将清空当前 SSC 预测结果和口感结果。");
      if (!ok) {
        $("#sscModelSelect").value = state.selectedSscModelId || "";
        return;
      }
      clearSscPrediction();
    }
    if (hasActiveSample()) saveModelSelection().catch((error) => addLog(error.message, "WARN"));
    updateAnalysisButtonStates();
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
      if (hasActiveSample()) saveModelSelection().catch((error) => addLog(error.message, "WARN"));
      updateAnalysisButtonStates();
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
  $("#newSampleFruitType")?.addEventListener("change", () => loadNewSampleCatalog().catch((error) => setText("newSampleHint", error.message)));
  $("#newSampleVariety")?.addEventListener("change", () => loadNewSampleCatalog().catch((error) => setText("newSampleHint", error.message)));

  $("#refreshPorts")?.addEventListener("click", () => {
    state.devicePrep.connect = true;
    setPill("serialStatus", "串口: 离线连接检查通过", "ok");
    setStepStatus("connect", "done");
    addLog("已完成连接检查：当前单片机未接入，按离线调试模式记录为准备完成。", "WARN");
    syncDevicePreparation().catch((error) => addLog(error.message, "WARN"));
  });

  $("#startWorkflow")?.addEventListener("click", () => {
    if (!requireDevicePreparation()) return;
    if (!requireActiveSample()) return;
    switchView("capture", "sample");
    setStepStatus("sample", "running");
    addLog("检测流程已启动：按离线模式进入样品采集。");
  });

  $("#emergencyStop")?.addEventListener("click", () => {
    setPill("motorStatus", "电机: 已停止", "warn");
    setPill("lightStatus", "光源: 已关闭", "warn");
    addLog("紧急停止已触发：模拟关闭电机与光源。", "WARN");
  });

  $("#startSscAnalysis")?.addEventListener("click", runSscAnalysis);
  $("#startAcidAnalysis")?.addEventListener("click", runAcidAnalysis);
  $("#evaluateTaste")?.addEventListener("click", () => updateTaste(true));
  $("#saveCameraSettings")?.addEventListener("click", saveCameraSettings);
  $("#resetCameraSettings")?.addEventListener("click", resetCameraSettings);
  $("#confirmCalibration")?.addEventListener("click", () => confirmCalibrationCheck().catch((error) => addLog(error.message, "WARN")));
  $("#shapeMode")?.addEventListener("change", (event) => updateShapeMode(event.target.value));
  document.querySelectorAll('input[name="dataSource"]').forEach((input) => {
    input.addEventListener("change", (event) => handleDataSourceChange(event.target.value));
  });
  ["#colorDir", "#depthDir"].forEach((selector) => {
    $(selector)?.addEventListener("change", () => {
      if (state.analysisDataDir) loadSampleFolder(state.analysisDataDir, { source: state.dataSource });
    });
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
  resetCaptureStepStatuses();
  renderCalibrationStatus();
  updateDevicePreparationControls();
  updateShapeMode();
  try {
    const status = await api("/api/status");
    if (status.devicePrep) state.devicePrep = { ...state.devicePrep, ...status.devicePrep };
    renderDevicePreparationStatus();
    applySampleSessionState(status);
    state.saveRootDir = status.saveRootDir || state.saveRootDir || "";
    state.captureStarted = Boolean(status.captureStarted);
    state.currentCaptureDir = status.currentCaptureDir || "";
    state.currentCaptureValid = Boolean(status.currentCaptureValid && state.currentCaptureDir);
    state.analysisDataDir = status.analysisDataDir || status.sampleDataset || "";
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
  } catch (error) {
    addLog(`后端未连接: ${error.message}`, "ERROR");
  }
});
