const studio = {
  datasets: [],
  selectedDatasetId: "",
  selectedDatasetVersionId: "",
  selectedSampleId: "",
  selectedExperimentId: "",
  latestFeature: null,
  jobTimer: null,
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
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
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
  const cls = status === "Production" || status === "complete" || status === "Completed" ? "good" : status === "Failed" || status === "Archived" ? "bad" : "warn";
  return `<span class="badge ${cls}">${status || "--"}</span>`;
}

function fmt(value, digits = 4) {
  if (value === null || value === undefined || value === "") return "--";
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits) : String(value);
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
      <td><small>${ds.storage_path}</small></td>
    </tr>
  `).join("");
  $("#datasetRows").innerHTML = rows || `<tr><td colspan="8" class="empty">暂无数据集</td></tr>`;
  text("datasetState", studio.datasets.length ? `${studio.datasets.length} datasets` : "Empty");
  const select = $("#datasetSelect");
  select.innerHTML = studio.datasets.map((ds) => `<option value="${ds.dataset_id}">${ds.dataset_name}</option>`).join("");
  if (!studio.selectedDatasetId && studio.datasets[0]) studio.selectedDatasetId = studio.datasets[0].dataset_id;
  select.value = studio.selectedDatasetId;
  await loadDatasetVersions().catch(() => {});
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
}

async function importSamples() {
  const datasetId = currentDatasetId();
  const sourcePath = $("#sourceSamplePath")?.value.trim() || "";
  const result = await api("/api/model-studio/samples/import", { method: "POST", body: JSON.stringify({ datasetId, sourcePath }) });
  toast(`新样品 ${result.result.newSamples} · 已有 ${result.result.existingSamples} · 冲突 ${result.result.conflicts}`);
  await refreshAll();
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
}

async function loadSamples() {
  const datasetId = currentDatasetId();
  const query = encodeURIComponent($("#sampleQuery").value.trim());
  const payload = await api(`/api/model-studio/samples?datasetId=${encodeURIComponent(datasetId)}&query=${query}&limit=80`);
  const items = payload.samples.items || [];
  const selected = studio.datasets.find((ds) => ds.dataset_id === datasetId);
  text("sampleDatasetName", selected ? selected.dataset_name : datasetId);
  $("#sampleRows").innerHTML = items.map((sample) => `
    <tr data-sample="${sample.sample_id}">
      <td><b>${sample.sample_id}</b></td><td>${sample.rgb_count}</td><td>${sample.multispectral_count}</td>
      <td>${sample.dark_count}</td><td>${sample.white_count}</td>
      <td>${sample.ssc ?? "--"}</td><td>${sample.ta ?? "--"}</td><td>${sample.ph ?? "--"}</td>
      <td>${badge(sample.include_status || "Included")}<br><small>${sample.exclude_reason || ""}</small></td>
      <td>${badge(sample.data_status)}</td>
    </tr>
  `).join("") || `<tr><td colspan="10" class="empty">暂无样品</td></tr>`;
  document.querySelectorAll("[data-sample]").forEach((row) => row.addEventListener("click", () => {
    studio.selectedSampleId = row.dataset.sample;
    text("sampleDatasetName", `${selected ? selected.dataset_name : datasetId} · ${studio.selectedSampleId}`);
  }));
}

async function importLabels() {
  const datasetId = currentDatasetId();
  const labelsCsvPath = $("#labelsPath").value.trim();
  const payload = await api("/api/model-studio/labels/import", { method: "POST", body: JSON.stringify({ datasetId, labelsCsvPath }) });
  toast(`已导入 ${payload.result.imported} 条标签`);
  await refreshAll();
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
}

async function qualityCheck() {
  const datasetId = currentDatasetId();
  const payload = await api(`/api/model-studio/quality?datasetId=${encodeURIComponent(datasetId)}`);
  $("#qualityReport").textContent = JSON.stringify(payload.quality, null, 2);
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
  $("#jobProgress i").style.width = `${Number(job.progress || 0)}%`;
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
  $("#importSamples").addEventListener("click", () => importSamples().catch((error) => toast(error.message)));
  $("#createDatasetVersion").addEventListener("click", () => createDatasetVersion().catch((error) => toast(error.message)));
  $("#refreshDatasets").addEventListener("click", () => loadDatasets().catch((error) => toast(error.message)));
  $("#datasetSelect").addEventListener("change", () => { studio.selectedDatasetId = $("#datasetSelect").value; studio.selectedDatasetVersionId = ""; loadDatasetVersions().then(loadSamples).catch((error) => toast(error.message)); });
  $("#datasetVersionSelect").addEventListener("change", () => { studio.selectedDatasetVersionId = $("#datasetVersionSelect").value; });
  $("#loadSamples").addEventListener("click", () => loadSamples().catch((error) => toast(error.message)));
  $("#importLabels").addEventListener("click", () => importLabels().catch((error) => toast(error.message)));
  $("#runQualityCheck").addEventListener("click", () => qualityCheck().catch((error) => toast(error.message)));
  $("#updateSampleStatus").addEventListener("click", () => updateSampleStatus().catch((error) => toast(error.message)));
  $("#generateFeatures").addEventListener("click", () => generateFeatures().catch((error) => toast(error.message)));
  $("#exportFeatureHint").addEventListener("click", () => toast(studio.latestFeature?.featureCsv || "尚未生成 features.csv"));
  $("#createExperiment").addEventListener("click", () => createExperiment().catch((error) => toast(error.message)));
  $("#startTraining").addEventListener("click", () => startTraining().catch((error) => toast(error.message)));
  $("#cloneExperiment").addEventListener("click", () => cloneExperiment().catch((error) => toast(error.message)));
  $("#refreshJobs").addEventListener("click", () => refreshJobs().catch((error) => toast(error.message)));
  await refreshAll().catch((error) => toast(error.message));
});
