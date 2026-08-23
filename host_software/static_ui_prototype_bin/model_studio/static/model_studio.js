const studio = {
  datasets: [],
  selectedDatasetId: "",
  selectedDatasetVersionId: "",
  selectedSampleId: "",
  selectedExperimentId: "",
  latestFeature: null,
  jobTimer: null,
  labelDirty: false,
  selectedSample: null,
  prepStep: "create",
  latestQuality: null,
};

const $ = (selector) => document.querySelector(selector);

function text(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function selectFolderPath({ purpose = "folder", initial = "" } = {}) {
  const query = new URLSearchParams({ purpose, initial });
  return api(`/api/select-folder?${query.toString()}`);
}

async function selectFilePath({ purpose = "file", initial = "" } = {}) {
  const query = new URLSearchParams({ purpose, initial });
  return api(`/api/select-file?${query.toString()}`);
}

function setPathDisplay(selector, value = "") {
  const node = $(selector);
  if (!node) return;
  node.value = value || "";
  node.title = value || "";
}

function toast(message) {
  const node = $("#toast");
  if (!node) return;
  node.textContent = message;
  node.hidden = false;
  window.clearTimeout(node._timer);
  node._timer = window.setTimeout(() => { node.hidden = true; }, 2600);
}

function switchView(view) {
  if (studio.labelDirty && !window.confirm("当前标签尚未保存，是否放弃修改？")) return;
  studio.labelDirty = false;
  document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.dataset.page === view));
  document.querySelectorAll(".nav").forEach((nav) => nav.classList.toggle("active", nav.dataset.view === view));
  const names = {
    dashboard: "总览 Dashboard",
    datasets: "数据集",
    samples: "样品与标签",
    workspace: "训练工作区",
    features: "特征工程",
    experiments: "训练实验",
    models: "模型库",
    logs: "任务日志",
  };
  text("activeViewName", names[view] || view);
  text("pageTitle", names[view] || view);
}

function badge(status) {
  const cls = ["Production", "complete", "Completed", "Complete", "Valid"].includes(status) ? "good" : ["Failed", "Archived", "Invalid"].includes(status) ? "bad" : "warn";
  return `<span class="badge ${cls}">${status || "--"}</span>`;
}

function fmt(value, digits = 4) {
  if (value === null || value === undefined || value === "") return "--";
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits) : String(value);
}

function selectedDataset() {
  return studio.datasets.find((ds) => ds.dataset_id === studio.selectedDatasetId) || null;
}

function latestDatasetVersion(dataset = selectedDataset()) {
  return dataset?.versions?.[0] || null;
}

function setPrepStep(step) {
  studio.prepStep = step;
  const titles = {
    create: "当前步骤：创建 Dataset",
    import: "当前步骤：导入样品",
    labels: "当前步骤：标签录入",
    quality: "当前步骤：数据质量检查",
    version: "当前步骤：创建 Dataset Version",
  };
  text("prepStepTitle", titles[step] || titles.create);
  document.querySelectorAll("[data-prep-pane]").forEach((pane) => pane.classList.toggle("active", pane.dataset.prepPane === step));
  renderPreparationWorkflow();
}

function statusClass(status) {
  return status === "completed" ? "completed" : status === "warning" ? "warning" : status === "failed" ? "failed" : "pending";
}

function renderPreparationWorkflow() {
  const dataset = selectedDataset();
  const hasDataset = Boolean(dataset);
  const sampleCount = Number(dataset?.sample_count || 0);
  const labelCount = Number(dataset?.label_count || 0);
  const hasVersion = Boolean(latestDatasetVersion(dataset));
  const states = {
    create: hasDataset ? "completed" : "pending",
    import: !hasDataset ? "pending" : sampleCount > 0 ? "completed" : "warning",
    labels: sampleCount <= 0 ? "pending" : labelCount > 0 ? "completed" : "warning",
    quality: sampleCount <= 0 ? "pending" : studio.latestQuality ? "completed" : "warning",
    version: sampleCount <= 0 ? "pending" : hasVersion ? "completed" : "warning",
  };
  document.querySelectorAll("[data-prep-step]").forEach((button) => {
    const key = button.dataset.prepStep;
    button.classList.toggle("current", key === studio.prepStep);
    button.dataset.status = key === studio.prepStep ? "current" : states[key];
    button.classList.remove("completed", "warning", "failed", "pending");
    button.classList.add(key === studio.prepStep ? "current" : statusClass(states[key]));
  });
}

function qualityRow(label, value, state = "pending") {
  return `<div class="quality-row ${state}"><span>${label}</span><b>${value}</b></div>`;
}

function renderDatasetSummary() {
  const dataset = selectedDataset();
  const latest = latestDatasetVersion(dataset);
  text("datasetState", studio.datasets.length ? `${studio.datasets.length} datasets` : "Empty");
  text("datasetListState", studio.datasets.length ? `${studio.datasets.length} datasets` : "Empty");
  text("summaryDatasetName", dataset ? dataset.dataset_name : "--");
  text("summarySampleCount", dataset ? Number(dataset.sample_count || 0) : 0);
  text("summaryLabelCount", dataset ? Number(dataset.label_count || 0) : 0);
  text("summaryLatestVersion", latest ? `${latest.version_name} · ${latest.sample_count} samples` : "--");
  text("summaryDatasetStatus", dataset ? (Number(dataset.dirty || 0) ? "Changed" : dataset.calibration_status || "Ready") : "--");
  text("prepCurrentDataset", dataset ? `${dataset.dataset_name} (${dataset.dataset_id})` : "请先创建或选择 Dataset");
  text("prepFruitVariety", dataset ? `${dataset.fruit_type || "--"} / ${dataset.variety || "generic"}` : "--");
  const sampleCount = Number(dataset?.sample_count || 0);
  const labelCount = Number(dataset?.label_count || 0);
  const quality = studio.latestQuality;
  const sscReady = quality && sampleCount ? sampleCount - Number(quality.missingSSC?.length || 0) : null;
  const taReady = quality && sampleCount ? sampleCount - Number(quality.missingTA?.length || 0) : null;
  const phReady = quality && sampleCount ? sampleCount - Number(quality.missingPH?.length || 0) : null;
  const html = [
    qualityRow("Dataset", dataset ? "Ready" : "Pending", dataset ? "completed" : "pending"),
    qualityRow("Samples", sampleCount, sampleCount > 0 ? "completed" : "warning"),
    qualityRow("Labels", labelCount, labelCount > 0 ? "completed" : sampleCount > 0 ? "warning" : "pending"),
    qualityRow("SSC Ready", sscReady === null ? "--" : `${sscReady} / ${sampleCount}`, sscReady === null ? "pending" : sscReady > 0 ? "completed" : "warning"),
    qualityRow("TA Ready", taReady === null ? "--" : `${taReady} / ${sampleCount}`, taReady === null ? "pending" : taReady > 0 ? "completed" : "warning"),
    qualityRow("pH Ready", phReady === null ? "--" : `${phReady} / ${sampleCount}`, phReady === null ? "pending" : phReady > 0 ? "completed" : "warning"),
    qualityRow("Latest Version", latest ? latest.version_name : "--", latest ? "completed" : sampleCount > 0 ? "warning" : "pending"),
    qualityRow("Status", dataset ? (Number(dataset.dirty || 0) ? "Changed" : dataset.calibration_status || "Ready") : "--", Number(dataset?.dirty || 0) ? "warning" : dataset ? "completed" : "pending"),
  ].join("");
  const summary = $("#datasetQualitySummary");
  if (summary) summary.innerHTML = html;
  const readiness = $("#versionReadiness");
  if (readiness) {
    readiness.className = `readiness-card ${sampleCount > 0 ? "ready" : "blocked"}`;
    readiness.innerHTML = sampleCount > 0
      ? `<strong>Ready for snapshot</strong><span>Samples ${sampleCount} · Labels ${labelCount} · Latest ${latest ? latest.version_name : "none"}</span>`
      : `<strong>无法创建用于训练的有效版本</strong><span>当前 Dataset 没有样品。请先完成“导入样品”。</span>`;
  }
  const versionButton = $("#createDatasetVersion");
  if (versionButton) versionButton.disabled = !dataset || sampleCount <= 0;
  renderPreparationWorkflow();
}

function renderSampleQualitySummary(sample = studio.selectedSample) {
  const target = $("#sampleQualitySummary");
  if (!target) return;
  if (!sample) {
    target.innerHTML = [
      qualityRow("RGB", "--"),
      qualityRow("Bands", "--"),
      qualityRow("Calibration", "--"),
      qualityRow("Labels", "--"),
      qualityRow("Use Status", "--"),
    ].join("");
    return;
  }
  const rgb = Number(sample.rgb_count || 0);
  const ms = Number(sample.multispectral_count || 0);
  const dark = Number(sample.dark_count || 0);
  const white = Number(sample.white_count || 0);
  target.innerHTML = [
    qualityRow("RGB", rgb, rgb > 0 ? "completed" : "warning"),
    qualityRow("Bands", ms, ms > 0 ? "completed" : "warning"),
    qualityRow("Calibration", `Dark ${dark} / White ${white}`, dark > 0 && white > 0 ? "completed" : "warning"),
    qualityRow("Labels", sample.label_status || "Missing", sample.label_status === "Complete" ? "completed" : "warning"),
    qualityRow("Use Status", sample.include_status || "Included", sample.include_status === "Excluded" ? "warning" : "completed"),
  ].join("");
}

function switchSampleTab(tab) {
  document.querySelectorAll("[data-sample-tab]").forEach((button) => button.classList.toggle("active", button.dataset.sampleTab === tab));
  document.querySelectorAll("[data-sample-pane]").forEach((pane) => pane.classList.toggle("active", pane.dataset.samplePane === tab));
}

async function loadDashboard() {
  const payload = await api("/api/model-studio/dashboard");
  const dashboard = payload.dashboard;
  text("dbPath", `SQLite: ${dashboard.databasePath}`);
  text("countDatasets", dashboard.counts.datasets);
  text("countVersions", dashboard.counts.datasetVersions || 0);
  text("countSamples", dashboard.counts.samples);
  text("countLabels", dashboard.counts.labels);
  text("countExperiments", dashboard.counts.experiments);
  text("countJobs", dashboard.counts.trainingJobs || 0);
  text("countProduction", dashboard.counts.publishedModels || dashboard.counts.productionModels || 0);
  text("countDefault", dashboard.counts.defaultModels || 0);
  text("countReview", dashboard.counts.modelsNeedingReview || 0);
  $("#productionModels").innerHTML = dashboard.productionModels.length
    ? dashboard.productionModels.map((model) => `<div class="model-card"><b>${model.target.toUpperCase()}</b> ${model.model_type} · ${model.preprocessing} · ${model.version}<br><small>${model.model_name}</small></div>`).join("")
    : "暂无已发布模型";
  $("#filterRows").innerHTML = dashboard.filterConfig.map((band) => `
    <tr>
      <td>${band.filter_position}</td><td>${band.wavelength_nm} nm</td><td>${band.bandwidth_nm ?? "--"}</td>
      <td>${band.exposure_ms ?? "--"}</td><td>${band.gain ?? "--"}</td><td>${band.enabled ? badge("Enabled") : badge("Disabled")}</td>
    </tr>
  `).join("");
}

async function loadDatasets() {
  const payload = await api("/api/model-studio/datasets");
  studio.datasets = payload.datasets || [];
  const rows = studio.datasets.map((ds) => `
    <tr data-dataset="${ds.dataset_id}">
      <td><b>${ds.dataset_name}</b><br><small>${ds.dataset_id}</small></td>
      <td>${ds.fruit_type || "--"}</td>
      <td>${ds.variety || "generic"}</td>
      <td>${ds.sample_count || 0}</td>
      <td>${ds.label_count || 0}</td>
      <td>${(ds.versions || []).map((v) => v.version_name).join(", ") || "--"}</td>
      <td>${Number(ds.dirty || 0) ? badge("Dataset Changed") : badge(ds.calibration_status)}</td>
      <td><small>${ds.local_path || ds.storage_path}</small></td>
    </tr>
  `).join("");
  $("#datasetRows").innerHTML = rows || `<tr><td colspan="8" class="empty">暂无数据集</td></tr>`;
  const select = $("#datasetSelect");
  select.innerHTML = studio.datasets.map((ds) => `<option value="${ds.dataset_id}">${ds.dataset_name}</option>`).join("");
  if (!studio.selectedDatasetId && studio.datasets[0]) studio.selectedDatasetId = studio.datasets[0].dataset_id;
  select.value = studio.selectedDatasetId;
  document.querySelectorAll("[data-dataset]").forEach((row) => row.addEventListener("click", async () => {
    studio.selectedDatasetId = row.dataset.dataset;
    studio.selectedDatasetVersionId = "";
    studio.latestQuality = null;
    if ($("#datasetSelect")) $("#datasetSelect").value = studio.selectedDatasetId;
    setPrepStep(Number(selectedDataset()?.sample_count || 0) > 0 ? "labels" : "import");
    await loadDatasetVersions().catch(() => {});
    await loadSamples().catch(() => {});
    renderDatasetSummary();
  }));
  await loadDatasetVersions().catch(() => {});
  renderDatasetSummary();
}

async function createDataset() {
  const payload = {
    datasetName: $("#datasetName").value.trim(),
    fruitType: $("#fruitType").value.trim(),
    variety: $("#variety").value.trim(),
    storagePath: $("#storagePath").value.trim(),
    description: $("#datasetDescription").value.trim(),
  };
  const result = await api("/api/model-studio/datasets", { method: "POST", body: JSON.stringify(payload) });
  studio.selectedDatasetId = result.dataset.dataset_id;
  toast("Dataset 已创建");
  await refreshAll();
  setPrepStep("import");
}

async function importSamples() {
  const datasetId = currentDatasetId();
  const sourcePath = $("#sourceSamplePath")?.value.trim() || "";
  text("sampleImportReport", "正在导入样品...");
  let result = await api("/api/model-studio/samples/import", { method: "POST", body: JSON.stringify({ datasetId, sourcePath, duplicatePolicy: "skip" }) });
  if (result.result.conflicts && window.confirm("该样品可能已经存在。是否作为新样品导入？")) {
    result = await api("/api/model-studio/samples/import", { method: "POST", body: JSON.stringify({ datasetId, sourcePath, duplicatePolicy: "new" }) });
  }
  $("#sampleImportReport").textContent = JSON.stringify(result.result, null, 2);
  toast(`新样品 ${result.result.newSamples} · 已有 ${result.result.existingSamples} · 冲突 ${result.result.conflicts}`);
  await refreshAll();
  if (Number(result.result.imported || result.result.newSamples || 0) > 0) setPrepStep("labels");
}

async function selectDatasetSource() {
  const payload = await selectFolderPath({
    purpose: "model-studio-source",
    initial: $("#storagePath")?.value.trim() || $("#sourceSamplePath")?.value.trim() || "",
  });
  if (payload.path) {
    setPathDisplay("#storagePath", payload.path);
    toast("默认导入来源已选择");
  }
}

async function selectSampleFolder() {
  const payload = await selectFolderPath({
    purpose: "model-studio-sample",
    initial: $("#sourceSamplePath")?.value.trim() || $("#storagePath")?.value.trim() || "",
  });
  if (payload.path) {
    setPathDisplay("#sourceSamplePath", payload.path);
    await validateSampleFolder();
  }
}

async function validateSampleFolder() {
  const sourcePath = $("#sourceSamplePath")?.value.trim() || "";
  if (!sourcePath) throw new Error("请先选择样品文件夹");
  const payload = await api("/api/model-studio/samples/validate", { method: "POST", body: JSON.stringify({ sourcePath }) });
  $("#sampleImportReport").textContent = JSON.stringify(payload.validation, null, 2);
  toast(`导入目录状态：${payload.validation.status}`);
  setPrepStep("import");
}

function currentDatasetId() {
  const id = $("#datasetSelect")?.value || studio.selectedDatasetId;
  if (!id) throw new Error("请先创建或选择 Dataset");
  studio.selectedDatasetId = id;
  return id;
}

async function loadDatasetVersions() {
  const datasetId = currentDatasetId();
  const payload = await api(`/api/model-studio/dataset-versions?datasetId=${encodeURIComponent(datasetId)}`);
  const versions = payload.versions || [];
  const select = $("#datasetVersionSelect");
  if (select) {
    select.innerHTML = versions.map((version) => `<option value="${version.dataset_version_id}">${version.version_name} · ${version.sample_count} samples</option>`).join("");
    if (!studio.selectedDatasetVersionId && versions[0]) studio.selectedDatasetVersionId = versions[0].dataset_version_id;
    select.value = studio.selectedDatasetVersionId || "";
  }
  const latest = versions[0];
  text("wsDatasetVersion", latest ? latest.version_name : "--");
  text("wsSamples", latest ? latest.sample_count : "--");
  text("wsLabels", latest ? latest.label_count : "--");
}

async function createDatasetVersion() {
  const datasetId = currentDatasetId();
  const payload = await api("/api/model-studio/dataset-versions", {
    method: "POST",
    body: JSON.stringify({ datasetId, description: $("#datasetDescription")?.value.trim() || "" }),
  });
  studio.selectedDatasetVersionId = payload.version.dataset_version_id;
  toast(`${payload.version.version_name} 已创建`);
  await refreshAll();
  setPrepStep("version");
}

async function loadSamples() {
  const datasetId = currentDatasetId();
  const query = encodeURIComponent($("#sampleQuery").value.trim());
  const payload = await api(`/api/model-studio/samples?datasetId=${encodeURIComponent(datasetId)}&query=${query}&limit=80`);
  const items = payload.samples.items || [];
  const selected = studio.datasets.find((ds) => ds.dataset_id === datasetId);
  text("sampleDatasetName", selected ? selected.dataset_name : datasetId);
  $("#sampleRows").innerHTML = items.map((sample) => `
    <tr data-sample="${sample.sample_id}" class="${sample.sample_id === studio.selectedSampleId ? "selected-row" : ""}">
      <td><b>${sample.sample_id}</b></td><td>${sample.rgb_count}</td><td>${sample.multispectral_count}</td>
      <td>${sample.dark_count}</td><td>${sample.white_count}</td>
      <td>${sample.ssc ?? "--"}</td><td>${sample.ta ?? "--"}</td><td>${sample.ph ?? "--"}</td>
      <td>${badge(sample.label_status || "Missing")}</td>
      <td>${badge(sample.include_status || "Included")}<br><small>${sample.exclude_reason || ""}</small></td>
      <td>${badge(sample.data_status)}</td>
    </tr>
  `).join("") || `<tr><td colspan="11" class="empty">暂无样品</td></tr>`;
  if ($("#sampleEmptyState")) $("#sampleEmptyState").hidden = items.length > 0;
  document.querySelectorAll("[data-sample]").forEach((row) => row.addEventListener("click", async () => {
    if (studio.labelDirty && !window.confirm("当前标签尚未保存，是否放弃修改？")) return;
    studio.selectedSampleId = row.dataset.sample;
    text("sampleDatasetName", `${selected ? selected.dataset_name : datasetId} · ${studio.selectedSampleId}`);
    await loadSampleDetail();
  }));
  if (!items.some((sample) => sample.sample_id === studio.selectedSampleId)) {
    studio.selectedSampleId = "";
    studio.selectedSample = null;
    renderSampleQualitySummary(null);
  }
}

async function loadSampleDetail() {
  const datasetId = currentDatasetId();
  if (!studio.selectedSampleId) return;
  const payload = await api(`/api/model-studio/samples?datasetId=${encodeURIComponent(datasetId)}&sampleId=${encodeURIComponent(studio.selectedSampleId)}`);
  studio.selectedSample = payload.sample;
  text("detailSampleId", payload.sample.sample_id);
  text("detailLocalPath", payload.sample.local_path || payload.sample.storage_path || "--");
  text("detailSourcePath", payload.sample.source_path || "--");
  $("#labelSsc").value = payload.sample.ssc ?? "";
  $("#labelTa").value = payload.sample.ta ?? "";
  $("#labelPh").value = payload.sample.ph ?? "";
  if ($("#sampleIncludeStatus")) $("#sampleIncludeStatus").value = payload.sample.include_status || "Included";
  if ($("#sampleStatusReason")) $("#sampleStatusReason").value = payload.sample.exclude_reason || "";
  studio.labelDirty = false;
  text("labelSaveState", payload.sample.label_status || "Missing");
  renderSampleQualitySummary(payload.sample);
}

function markLabelDirty() {
  if (!studio.selectedSampleId) return;
  studio.labelDirty = true;
  text("labelSaveState", "未保存");
}

async function saveSampleLabel() {
  const datasetId = currentDatasetId();
  if (!studio.selectedSampleId) throw new Error("请先选择 Sample");
  const payload = await api("/api/model-studio/labels/save", {
    method: "POST",
    body: JSON.stringify({
      datasetId,
      sampleId: studio.selectedSampleId,
      ssc: $("#labelSsc").value.trim(),
      ta: $("#labelTa").value.trim(),
      ph: $("#labelPh").value.trim(),
    }),
  });
  studio.selectedSample = payload.sample;
  studio.labelDirty = false;
  text("labelSaveState", payload.sample.label_status || "Saved");
  renderSampleQualitySummary(payload.sample);
  toast("标签已保存");
  await refreshAll();
}

async function deleteSelectedSample(deleteLocalCopy = false) {
  const datasetId = currentDatasetId();
  if (!studio.selectedSampleId) throw new Error("请先选择 Sample");
  const message = deleteLocalCopy
    ? "将删除 Model Studio 数据库记录和本地托管副本，但不会删除原始 source_path。确认继续？"
    : "只删除 Model Studio 数据库记录，不删除本地副本或原始 source_path。确认继续？";
  if (!window.confirm(message)) return;
  if (deleteLocalCopy && !window.confirm("危险操作二次确认：本地托管副本会被删除，原始拍摄目录仍会保留。")) return;
  const payload = await api("/api/model-studio/samples/delete", {
    method: "POST",
    body: JSON.stringify({ datasetId, sampleId: studio.selectedSampleId, deleteLocalCopy }),
  });
  studio.selectedSampleId = "";
  studio.selectedSample = null;
  studio.labelDirty = false;
  text("detailSampleId", "--");
  text("detailLocalPath", "--");
  text("detailSourcePath", payload.result.sourcePath || "--");
  text("labelSaveState", "已删除");
  toast(payload.result.localDeleted ? "样品记录和本地副本已删除，原始目录未删除" : "样品记录已删除");
  await refreshAll();
}

async function importLabels() {
  const datasetId = currentDatasetId();
  const labelsCsvPath = $("#labelsPath").value.trim();
  const payload = await api("/api/model-studio/labels/import", { method: "POST", body: JSON.stringify({ datasetId, labelsCsvPath }) });
  toast(`已导入 ${payload.result.imported} 条标签`);
  await refreshAll();
}

async function selectLabelsCsv() {
  const payload = await selectFilePath({
    purpose: "labels-csv",
    initial: $("#labelsPath")?.value.trim() || $("#storagePath")?.value.trim() || "",
  });
  if (payload.path) {
    setPathDisplay("#labelsPath", payload.path);
    toast("labels.csv 已选择");
  }
}

async function updateSampleStatus() {
  const datasetId = currentDatasetId();
  const sampleId = studio.selectedSampleId || $("#sampleQuery").value.trim();
  if (!sampleId) throw new Error("请先点击样品行或输入 sample_id");
  await api("/api/model-studio/samples/status", {
    method: "POST",
    body: JSON.stringify({
      datasetId,
      sampleId,
      includeStatus: $("#sampleIncludeStatus").value,
      reason: $("#sampleStatusReason").value.trim(),
    }),
  });
  toast("样品状态已更新，当前 Dataset 已标记为 Changed");
  await refreshAll();
  if (studio.selectedSampleId) await loadSampleDetail().catch(() => {});
}

async function qualityCheck() {
  const datasetId = currentDatasetId();
  const payload = await api(`/api/model-studio/quality?datasetId=${encodeURIComponent(datasetId)}`);
  studio.latestQuality = payload.quality;
  $("#qualityReport").textContent = JSON.stringify(payload.quality, null, 2);
  renderDatasetSummary();
  setPrepStep("quality");
}

async function generateFeatures() {
  const datasetId = currentDatasetId();
  studio.selectedDatasetVersionId = $("#datasetVersionSelect")?.value || studio.selectedDatasetVersionId;
  const payload = await api("/api/model-studio/features", { method: "POST", body: JSON.stringify({ datasetId, datasetVersionId: studio.selectedDatasetVersionId }) });
  studio.latestFeature = payload.features;
  text("featureState", `${payload.features.rows} rows`);
  $("#featurePreview").innerHTML = `
    <div><b>features.csv</b><br><small>${payload.features.featureCsv}</small></div>
    <div>波段：${payload.features.wavelengths.join(", ")} nm</div>
    <div>失败：${payload.features.failures.length}</div>
  `;
  drawSpectrum(payload.features.wavelengths);
  toast("特征数据集已生成");
}

function drawSpectrum(wavelengths) {
  const canvas = $("#spectrumChart");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#22d3ee";
  ctx.fillStyle = "#93a4ba";
  ctx.lineWidth = 2;
  const values = wavelengths.map((_, i) => 0.25 + i * 0.08);
  ctx.beginPath();
  values.forEach((value, i) => {
    const x = 40 + i * ((canvas.width - 80) / Math.max(values.length - 1, 1));
    const y = canvas.height - 38 - value * 150;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
    ctx.fillText(`${wavelengths[i]}`, x - 12, canvas.height - 14);
  });
  ctx.stroke();
  ctx.fillText("Reflectance", 16, 22);
}

async function createExperiment() {
  const datasetId = currentDatasetId();
  studio.selectedDatasetVersionId = $("#datasetVersionSelect")?.value || studio.selectedDatasetVersionId;
  const models = [...document.querySelectorAll('input[name="modelType"]:checked')].map((item) => item.value);
  const preprocessing = [...document.querySelectorAll('input[name="preprocess"]:checked')].map((item) => item.value);
  const payload = {
    datasetId,
    datasetVersionId: studio.selectedDatasetVersionId,
    experimentName: $("#experimentName").value.trim(),
    target: $("#targetSelect").value,
    description: $("#experimentDescription").value.trim(),
    models,
    preprocessing,
    validationMethod: $("#validationMethod").value,
  };
  const result = await api("/api/model-studio/experiments", { method: "POST", body: JSON.stringify(payload) });
  studio.selectedExperimentId = result.experiment.experiment_id;
  toast("训练实验已创建");
  await refreshExperiments();
}

async function startTraining() {
  if (!studio.selectedExperimentId) {
    await createExperiment();
  }
  const result = await api("/api/model-studio/jobs", { method: "POST", body: JSON.stringify({ experimentId: studio.selectedExperimentId }) });
  toast("训练任务已启动");
  pollJob(result.job.job_id);
}

async function cloneExperiment() {
  if (!studio.selectedExperimentId) throw new Error("请先创建或选择一个实验");
  const payload = await api("/api/model-studio/experiments/clone", {
    method: "POST",
    body: JSON.stringify({ experimentId: studio.selectedExperimentId }),
  });
  studio.selectedExperimentId = payload.experiment.experiment_id;
  toast("实验已复制，可修改配置后重新训练");
  await refreshExperiments();
}

async function refreshExperiments() {
  const payload = await api("/api/model-studio/experiments");
  if (!studio.selectedExperimentId && payload.experiments[0]) studio.selectedExperimentId = payload.experiments[0].experiment_id;
}

async function refreshJobs() {
  const payload = await api("/api/model-studio/jobs");
  const job = payload.jobs[0];
  if (job) renderJob(job);
}

function pollJob(jobId) {
  window.clearInterval(studio.jobTimer);
  studio.jobTimer = window.setInterval(async () => {
    const payload = await api(`/api/model-studio/jobs/${jobId}`);
    renderJob(payload.job);
    if (["Completed", "Failed", "Cancelled"].includes(payload.job.status)) {
      window.clearInterval(studio.jobTimer);
      await loadModels();
    }
  }, 1000);
}

function renderJob(job) {
  text("jobState", job.status);
  text("wsJob", `${job.status} / Run #${job.run_number || 1}`);
  const terminal = ["Completed", "Failed", "Cancelled"].includes(job.status);
  const progress = terminal ? 100 : Number(job.progress || 0);
  $("#jobProgress i").style.width = `${progress}%`;
  $("#jobLog").textContent = `${job.status} / ${job.step}\n${job.message || ""}\n\n${(job.logs || []).join("\n")}`;
  const results = job.result?.results || [];
  $("#resultRows").innerHTML = results.map((row) => `
    <tr><td>${row.preprocessing}</td><td>${row.model}</td><td>${fmt(row.r2)}</td><td>${fmt(row.rmse)}</td><td>${fmt(row.mae)}</td><td>${fmt(row.rpd)}</td><td>${badge("Candidate")}</td></tr>
  `).join("");
}

async function loadModels() {
  const payload = await api("/api/model-studio/models");
  $("#modelRows").innerHTML = (payload.models || []).map((model) => `
    <tr>
      <td><b>${model.display_name || model.model_name}</b><br><small>${model.model_id}</small></td>
      <td>${model.fruit_type || "--"}</td><td>${model.variety || "generic"}</td>
      <td>${model.target.toUpperCase()}</td><td>${model.model_type}</td><td>${model.preprocessing}</td>
      <td>${model.dataset_version_label || model.dataset_version_id || "--"}</td>
      <td>${model.version}</td><td>${fmt(model.r2)}</td><td>${fmt(model.rmse)}</td><td>${badge(model.status)}</td>
      <td>
        <button data-validate="${model.model_id}">验证</button>
        <button data-publish="${model.model_id}">发布</button>
        <button data-default="${model.model_id}">设为默认</button>
        <button data-retrain="${model.model_id}">重训</button>
        <button data-export="${model.model_id}">导出</button>
        <button class="danger" data-archive="${model.model_id}">归档</button>
      </td>
    </tr>
  `).join("") || `<tr><td colspan="12" class="empty">暂无模型</td></tr>`;
  document.querySelectorAll("[data-validate]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api("/api/model-studio/models/validate", { method: "POST", body: JSON.stringify(publishPayload(button.dataset.validate)) });
      toast("模型已标记为 Validated");
      await loadModels();
    });
  });
  document.querySelectorAll("[data-publish]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api("/api/model-studio/models/publish", { method: "POST", body: JSON.stringify(publishPayload(button.dataset.publish)) });
      toast($("#publishAsDefault").checked ? "模型已发布并设为默认" : "模型已发布");
      await refreshAll();
    });
  });
  document.querySelectorAll("[data-default]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api("/api/model-studio/models/default", { method: "POST", body: JSON.stringify({ modelId: button.dataset.default }) });
      toast("默认模型已更新");
      await refreshAll();
    });
  });
  document.querySelectorAll("[data-retrain]").forEach((button) => {
    button.addEventListener("click", async () => {
      const payload = await api("/api/model-studio/experiments/retrain", { method: "POST", body: JSON.stringify({ modelId: button.dataset.retrain, datasetVersionId: $("#datasetVersionSelect")?.value || "" }) });
      studio.selectedExperimentId = payload.experiment.experiment_id;
      switchView("experiments");
      toast("已创建重训实验，旧模型未被覆盖");
    });
  });
  document.querySelectorAll("[data-export]").forEach((button) => {
    button.addEventListener("click", async () => {
      const payload = await api("/api/model-studio/models/export", { method: "POST", body: JSON.stringify({ modelId: button.dataset.export }) });
      toast(payload.bundle.bundlePath);
    });
  });
  document.querySelectorAll("[data-archive]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api("/api/model-studio/models/archive", { method: "POST", body: JSON.stringify({ modelId: button.dataset.archive }) });
      toast("模型已归档");
      await loadModels();
    });
  });
}

function publishPayload(modelId) {
  return {
    modelId,
    displayName: $("#publishDisplayName")?.value.trim() || "",
    version: $("#publishVersion")?.value.trim() || "",
    tags: $("#publishTags")?.value.trim() || "",
    notes: $("#publishNotes")?.value.trim() || "",
    setDefault: Boolean($("#publishAsDefault")?.checked),
  };
}

async function loadLogs() {
  const payload = await api("/api/model-studio/logs");
  $("#operationLogs").textContent = (payload.logs || []).map((log) => `[${log.timestamp}] ${log.operation} ${log.resource_id || ""}\n${log.message}`).join("\n\n") || "暂无日志";
}

async function refreshAll() {
  await loadDashboard();
  await loadDatasets();
  await loadSamples().catch(() => {});
  await refreshExperiments().catch(() => {});
  await refreshJobs().catch(() => {});
  await loadModels();
  await loadLogs();
}

document.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".nav").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  $("#createDataset").addEventListener("click", () => createDataset().catch((error) => toast(error.message)));
  $("#selectDatasetSource").addEventListener("click", () => selectDatasetSource().catch((error) => {
    if (error.payload?.cancelled) return toast("已取消选择，原路径保持不变");
    toast(error.message);
  }));
  $("#selectSampleFolder").addEventListener("click", () => selectSampleFolder().catch((error) => {
    if (error.payload?.cancelled) return toast("已取消选择，原路径保持不变");
    toast(error.message);
  }));
  $("#validateSampleFolder").addEventListener("click", () => validateSampleFolder().catch((error) => toast(error.message)));
  $("#importSamples").addEventListener("click", () => importSamples().catch((error) => toast(error.message)));
  $("#createDatasetVersion").addEventListener("click", () => createDatasetVersion().catch((error) => toast(error.message)));
  $("#refreshDatasets").addEventListener("click", () => loadDatasets().catch((error) => toast(error.message)));
  $("#datasetSelect").addEventListener("change", () => {
    if (studio.labelDirty && !window.confirm("当前标签尚未保存，是否放弃修改？")) {
      $("#datasetSelect").value = studio.selectedDatasetId;
      return;
    }
    studio.labelDirty = false;
    studio.selectedDatasetId = $("#datasetSelect").value;
    studio.selectedDatasetVersionId = "";
    studio.latestQuality = null;
    loadDatasetVersions().then(loadSamples).then(renderDatasetSummary).catch((error) => toast(error.message));
  });
  $("#datasetVersionSelect").addEventListener("change", () => { studio.selectedDatasetVersionId = $("#datasetVersionSelect").value; });
  $("#loadSamples").addEventListener("click", () => loadSamples().catch((error) => toast(error.message)));
  $("#selectLabelsCsv").addEventListener("click", () => selectLabelsCsv().catch((error) => {
    if (error.payload?.cancelled) return toast("已取消选择，原路径保持不变");
    toast(error.message);
  }));
  $("#importLabels").addEventListener("click", () => importLabels().catch((error) => toast(error.message)));
  $("#runQualityCheck").addEventListener("click", () => qualityCheck().catch((error) => toast(error.message)));
  $("#qualityCheckFromPrep")?.addEventListener("click", () => qualityCheck().catch((error) => toast(error.message)));
  $("#updateSampleStatus").addEventListener("click", () => updateSampleStatus().catch((error) => toast(error.message)));
  $("#saveSampleLabel").addEventListener("click", () => saveSampleLabel().catch((error) => toast(error.message)));
  $("#deleteSampleRecord").addEventListener("click", () => deleteSelectedSample(false).catch((error) => toast(error.message)));
  $("#deleteSampleLocalCopy").addEventListener("click", () => deleteSelectedSample(true).catch((error) => toast(error.message)));
  ["labelSsc", "labelTa", "labelPh"].forEach((id) => document.getElementById(id).addEventListener("input", markLabelDirty));
  $("#generateFeatures").addEventListener("click", () => generateFeatures().catch((error) => toast(error.message)));
  $("#exportFeatureHint").addEventListener("click", () => toast(studio.latestFeature?.featureCsv || "尚未生成 features.csv"));
  $("#createExperiment").addEventListener("click", () => createExperiment().catch((error) => toast(error.message)));
  $("#startTraining").addEventListener("click", () => startTraining().catch((error) => toast(error.message)));
  $("#cloneExperiment").addEventListener("click", () => cloneExperiment().catch((error) => toast(error.message)));
  $("#refreshJobs").addEventListener("click", () => refreshJobs().catch((error) => toast(error.message)));
  document.querySelectorAll("[data-prep-step]").forEach((button) => {
    button.addEventListener("click", () => setPrepStep(button.dataset.prepStep));
  });
  document.querySelectorAll("[data-sample-tab]").forEach((button) => {
    button.addEventListener("click", () => switchSampleTab(button.dataset.sampleTab));
  });
  $("#goSamplesFromPrep")?.addEventListener("click", () => switchView("samples"));
  $("#goTrainingWorkspace")?.addEventListener("click", () => switchView("experiments"));
  $("#backToImportFromSamples")?.addEventListener("click", () => {
    switchView("datasets");
    setPrepStep("import");
  });
  await refreshAll().catch((error) => toast(error.message));
  setPrepStep(studio.datasets.length ? (Number(selectedDataset()?.sample_count || 0) > 0 ? "labels" : "import") : "create");
});

window.addEventListener("beforeunload", (event) => {
  if (!studio.labelDirty) return;
  event.preventDefault();
  event.returnValue = "";
});
