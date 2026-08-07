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
  viewer: null,
  imageBrowser: {
    images: [],
    index: 0,
  },
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

const shapeStepMap = {
  check: "load-rgbd",
  preprocess: "preprocess",
  images: "image-review",
  reconstruct: "reconstruct",
  filter: "filter",
  texture: "surface-texture",
  fusion: "fusion",
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

function addLog(message, level = "INFO") {
  const log = $("#runLog");
  if (!log) return;
  const stamp = new Date().toTimeString().slice(0, 8);
  log.textContent += `\n[${stamp}] [${level}] ${message}`;
  log.scrollTop = log.scrollHeight;
}

function parseNumbers(value) {
  return value
    .split(/[,，\s]+/)
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isFinite(item));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || payload.message || `HTTP ${response.status}`);
  }
  return payload;
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
    setText("pointcloudHint", "点云查看加载失败，显示静态预览图");
    addLog(`可旋转点云加载失败: ${error.message}`, "WARN");
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

function switchView(view, stepKey = null) {
  document.querySelectorAll(".view-page").forEach((page) => {
    page.classList.toggle("active", page.dataset.page === view);
  });
  setText("viewTitle", titles[view] || "功能模块");
  if (stepKey) setCurrentStep(stepKey);
  addLog(`切换到 ${titles[view] || view}`);
}

function runDeviceTest(type) {
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
  setText("statusNote", "硬件通信尚未接入，当前自检结果来自离线模拟。");
  addLog(item.log);
}

function updateCaptureProgress(step) {
  state.captureStep = Math.max(state.captureStep, step);
  const percent = Math.min(100, state.captureStep * 25);
  const progress = $("#captureProgress");
  if (progress) progress.style.width = `${percent}%`;
  setText("captureProgressText", `采集进度: ${Math.min(12, state.captureStep * 3)} / 12`);
  ["sample", "dark", "white", "rgb", "spectral", "integrity"].slice(0, state.captureStep + 1).forEach((key) => setStepStatus(key, "done"));
  addLog(`样品采集步骤 ${step} 已完成（离线模拟）。`);
}

function applySugar() {
  const values = parseNumbers($("#sscInput")?.value || "");
  if (!values.length) {
    addLog("未输入有效糖度数据。", "WARN");
    setStepStatus("sugar", "warning");
    return;
  }
  const avg = values.reduce((sum, value) => sum + value, 0) / values.length;
  state.ssc = avg;
  setText("resultSsc", `${avg.toFixed(2)} °Brix`);
  setText("resultSampleName", $("#sampleId")?.value || "--");
  setStepStatus("sugar", "done");
  addLog(`糖度分析完成：平均 SSC ${avg.toFixed(2)} °Brix。`);
  updateTaste(false);
}

function applyAcid() {
  const ta = Number($("#taInput")?.value);
  const ph = Number($("#phInput")?.value);
  if (Number.isFinite(ta)) {
    state.ta = ta;
    setText("resultTa", `${ta.toFixed(2)} %`);
  }
  if (Number.isFinite(ph)) {
    state.ph = ph;
    setText("resultPh", ph.toFixed(2));
  }
  if (!Number.isFinite(ta) && !Number.isFinite(ph)) {
    addLog("未输入有效酸度或 pH 数据。", "WARN");
    setStepStatus("acid", "warning");
    return;
  }
  setStepStatus("acid", "done");
  addLog("酸度分析结果已写入。");
  updateTaste(false);
}

function updateTaste(announce = true) {
  if (!Number.isFinite(state.ssc) || !Number.isFinite(state.ta) || state.ta <= 0) {
    if (announce) addLog("口感分析需要先输入糖度和酸度。", "WARN");
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
    setText("shapeStepLabel", "打开数据集文件夹选择器");
    const payload = await api("/api/select-dataset");
    if (payload.datasetDir) {
      $("#datasetDir").value = payload.datasetDir;
      $("#colorDir").value = "";
      $("#depthDir").value = "";
      addLog(`已选择数据集: ${payload.datasetDir}`);
      setStepStatus("load-rgbd", "done");
      setText("shapeStepLabel", "数据集文件夹已选择");
      await loadDatasetImages();
    }
  } catch (error) {
    addLog(error.message || "用户取消选择数据集。", "WARN");
    const picker = $("#datasetPicker");
    if (picker) {
      picker.value = "";
      picker.click();
    }
  }
}

async function uploadSelectedDataset(event) {
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
  setText("shapeStepLabel", "导入本地 RGB-D 数据集");
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
    $("#datasetDir").value = payload.datasetDir;
    $("#colorDir").value = "";
    $("#depthDir").value = "";
    setStepStatus("load-rgbd", "done");
    setText("shapeStepLabel", "数据集已导入");
    addLog(`已导入 ${payload.fileCount} 个文件: ${payload.datasetDir}`);
    await loadDatasetImages();
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

async function loadDatasetImages() {
  const datasetDir = $("#datasetDir")?.value || "";
  if (!datasetDir) return;
  const query = new URLSearchParams({
    datasetDir,
    colorDir: $("#colorDir")?.value || "",
    depthDir: $("#depthDir")?.value || "",
  });
  try {
    const payload = await api(`/api/dataset-images?${query.toString()}`);
    state.imageBrowser.images = payload.images || [];
    state.imageBrowser.index = 0;
    if (payload.colorDir) $("#colorDir").value = payload.colorDir;
    if (payload.depthDir) $("#depthDir").value = payload.depthDir;
    renderDatasetImage();
    setStepStatus("image-review", state.imageBrowser.images.length ? "done" : "warning");
    addLog(`图片浏览已加载 ${state.imageBrowser.images.length} 组 RGB-D 图片。`);
  } catch (error) {
    state.imageBrowser.images = [];
    state.imageBrowser.index = 0;
    renderDatasetImage();
    setStepStatus("image-review", "warning");
    addLog(`图片浏览未加载: ${error.message}`, "WARN");
  }
}

function renderDatasetImage() {
  const images = state.imageBrowser.images;
  const color = $("#colorPreview");
  const depth = $("#depthPreview");
  if (!images.length) {
    setText("imageBrowserCount", "当前数据集没有可浏览的 RGB-D 图片");
    setText("colorPreviewName", "彩色图");
    setText("depthPreviewName", "深度图");
    return;
  }
  const index = Math.max(0, Math.min(state.imageBrowser.index, images.length - 1));
  state.imageBrowser.index = index;
  const item = images[index];
  if (color) color.src = `${item.color.url}&t=${Date.now()}`;
  if (depth) depth.src = `${item.depth.url}&t=${Date.now()}`;
  setText("imageBrowserCount", `${index + 1} / ${images.length}`);
  setText("colorPreviewName", item.color.name || "彩色图");
  setText("depthPreviewName", item.depth.name || "深度图");
}

function stepDatasetImage(delta) {
  if (!state.imageBrowser.images.length) return;
  const count = state.imageBrowser.images.length;
  state.imageBrowser.index = (state.imageBrowser.index + delta + count) % count;
  renderDatasetImage();
}

async function runShapeAnalysis() {
  if (state.shapeJobId) {
    addLog("已有形态分析任务正在运行。", "WARN");
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
    const payload = await api("/api/analyze-shape", {
      method: "POST",
      body: JSON.stringify({
        datasetDir: $("#datasetDir")?.value || "",
        colorDir: $("#colorDir")?.value || "",
        depthDir: $("#depthDir")?.value || "",
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
  const order = ["load-rgbd", "preprocess", "image-review", "reconstruct", "filter", "surface-texture", "fusion", "measure", "volume", "confirm"];
  const currentIndex = order.indexOf(currentKey);
  order.forEach((key, index) => {
    if (index < currentIndex) setStepStatus(key, "done");
  });
}

function renderShapeResult(result) {
  if (!result) return;
  setText("metricDepth", `${Number(result.averageDepthMm).toFixed(2)} mm`);
  setText("metricDiameter", `${Number(result.diameterMm).toFixed(2)} mm`);
  setText("metricHeight", `${Number(result.heightMm).toFixed(2)} mm`);
  setText("metricVolume", `${Number(result.volumeMm3).toFixed(2)} mm³`);
  setText("metricWeight", `${Number(result.weightG).toFixed(2)} g`);
  renderTextureResult(result.texture);
  setText("resultShape", `点数 ${result.pointCount} / 体积 ${Number(result.volumeMm3).toFixed(2)} mm³`);
  setText("resultSummary", `形态分析成功，用时 ${result.elapsedSec}s，结果来自 Python 算法。`);
  setStepStatus("confirm", "done");
  $("#pointcloudPreview").src = `${result.previewUrl}?t=${Date.now()}`;
  loadPointcloudViewer(result.plyUrl);
  addLog(`形态分析成功：点数 ${result.pointCount}，体积 ${Number(result.volumeMm3).toFixed(2)} mm³。`);
}

function resetShapeStatus() {
  clearPointcloudViewer();
  resetTextureResult();
  ["load-rgbd", "preprocess", "image-review", "reconstruct", "filter", "surface-texture", "fusion", "measure", "volume", "confirm"].forEach((key) => {
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
    setStepStatus("surface-texture", "warning");
    return;
  }
  const bloom = `${Number(texture.bloomCoveragePercent).toFixed(2)}%`;
  setText("textureStatus", texture.message || "分析完成");
  setText("metricBloom", bloom);
  setText("metricBloomSide", bloom);
  setText("metricUniformity", `${Number(texture.colorUniformity).toFixed(1)}`);
  if (texture.previewUrl && $("#texturePreview")) {
    $("#texturePreview").src = `${texture.previewUrl}?t=${Date.now()}`;
  }
  setStepStatus("surface-texture", "done");
}

function resetTextureResult() {
  setText("textureStatus", "等待分析");
  setText("metricBloom", "--");
  setText("metricBloomSide", "--");
  setText("metricUniformity", "--");
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
  $("#runShapeAnalysis").disabled = false;
  $("#cancelShapeAnalysis").disabled = true;
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

  document.querySelectorAll(".task-step").forEach((button) => {
    button.dataset.status = button.dataset.status || "idle";
    button.addEventListener("click", () => switchView(button.dataset.view, button.dataset.stepKey));
  });

  document.querySelectorAll("[data-test]").forEach((button) => {
    button.addEventListener("click", () => runDeviceTest(button.dataset.test));
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
  });

  $("#refreshPorts")?.addEventListener("click", () => {
    setPill("serialStatus", "串口: 待调试", "warn");
    setStepStatus("connect", "warning");
    addLog("已刷新串口列表：当前单片机未接入，保持离线调试。", "WARN");
  });

  $("#startWorkflow")?.addEventListener("click", () => {
    switchView("capture", "sample");
    setStepStatus("sample", "running");
    addLog("检测流程已启动：按离线模式进入样品采集。");
  });

  $("#emergencyStop")?.addEventListener("click", () => {
    setPill("motorStatus", "电机: 已停止", "warn");
    setPill("lightStatus", "光源: 已关闭", "warn");
    addLog("紧急停止已触发：模拟关闭电机与光源。", "WARN");
  });

  $("#applySugar")?.addEventListener("click", applySugar);
  $("#applyAcid")?.addEventListener("click", applyAcid);
  $("#evaluateTaste")?.addEventListener("click", () => updateTaste(true));
  $("#selectDataset")?.addEventListener("click", selectDataset);
  $("#datasetPicker")?.addEventListener("change", uploadSelectedDataset);
  $("#builtinDataset")?.addEventListener("change", async (event) => {
    $("#datasetDir").value = event.target.value;
    $("#colorDir").value = "";
    $("#depthDir").value = "";
    setStepStatus("load-rgbd", "done");
    addLog(`已切换内置示例: ${event.target.options[event.target.selectedIndex].text}`);
    await loadDatasetImages();
  });
  $("#prevImage")?.addEventListener("click", () => stepDatasetImage(-1));
  $("#nextImage")?.addEventListener("click", () => stepDatasetImage(1));
  $("#refreshImages")?.addEventListener("click", loadDatasetImages);
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

  $("#exitButton")?.addEventListener("click", shutdownApp);

  window.setInterval(updateClock, 1000);
  updateClock();
  try {
    const status = await api("/api/status");
    if (status.sampleDatasets && $("#builtinDataset")) {
      const select = $("#builtinDataset");
      select.innerHTML = "";
      Object.values(status.sampleDatasets).forEach((item) => {
        const option = document.createElement("option");
        option.value = item.path;
        option.textContent = item.label;
        select.appendChild(option);
      });
    }
    if (status.sampleDataset && $("#datasetDir")) {
      $("#datasetDir").value = status.sampleDataset;
      if ($("#builtinDataset")) $("#builtinDataset").value = status.sampleDataset;
    }
    await loadDatasetImages();
    if (status.dependencies?.PIL && status.dependencies?.numpy) {
      addLog("Python 后端已连接，图像分析依赖可用。");
    }
    if (!status.dependencies?.cv2) {
      addLog("未检测到 OpenCV：点云重建需要 cv2，请检查 opencv-python 是否已随程序打包。", "WARN");
    }
  } catch (error) {
    addLog(`后端未连接: ${error.message}`, "ERROR");
  }
});
