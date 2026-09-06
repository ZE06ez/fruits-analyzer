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
    datasets: "Datasets",
    samples: "样品与标签",
    workspace: "Training",
    features: "特征工程",
    experiments: "Training",
    models: "Models",
    settings: "Settings",
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

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function isDefaultModel(model) {
  return Boolean(model?.isDefault || model?.is_default || model?.status === "Default");
}

function isPublishedModel(model) {
  return ["Published", "Default", "Production"].includes(model?.status);
}

function closeMenus() {
  document.querySelectorAll(".more-menu[open]").forEach((menu) => menu.removeAttribute("open"));
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
  const archiveButton = $("#archiveDataset");
  if (archiveButton) archiveButton.disabled = !dataset || Number(dataset?.archived || 0) === 1;
  const deleteButton = $("#deleteDatasetPermanently");
  if (deleteButton) deleteButton.disabled = !dataset;
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
  text("dbPath", "Model Studio");
  text("settingsDbPath", dashboard.databasePath);
  text("countDatasets", dashboard.counts.datasets);
  text("countSamples", dashboard.counts.samples);
  text("countRunning", dashboard.counts.runningTraining || 0);
  text("countProduction", dashboard.counts.publishedModels || dashboard.counts.productionModels || 0);
  text("countAttention", (dashboard.needsAttention || []).length);
  $("#productionModels").innerHTML = dashboard.productionModels.length
    ? dashboard.productionModels.slice(0, 8).map((model) => `<div class="model-card compact-card"><b>${model.display_name || model.model_name}</b><span>${model.fruit_type || "--"} / ${model.variety || "generic"} · ${model.target.toUpperCase()}</span><small>${model.model_type} · ${model.preprocessing} · ${model.version}</small></div>`).join("")
    : "暂无已发布模型";
  $("#attentionList").innerHTML = (dashboard.needsAttention || []).length
    ? dashboard.needsAttention.map((item) => `<div class="quality-row ${item.severity === "error" ? "failed" : "warning"}"><span>${item.kind}</span><b>${item.message}</b></div>`).join("")
    : "暂无需要处理的问题";
  $("#recentDatasets").innerHTML = (dashboard.recentDatasets || []).map((ds) => `<div class="compact-card"><b>${ds.dataset_name}</b><span>${ds.fruit_type || "--"} / ${ds.variety || "generic"} · ${ds.sample_count || 0} samples</span></div>`).join("") || `<div class="empty compact">暂无数据集</div>`;
  $("#recentTraining").innerHTML = (dashboard.recentJobs || []).map((job) => `<div class="compact-card"><b>${job.status} · Run #${job.run_number || 1}</b><span>${job.message || job.experiment_id}</span></div>`).join("") || `<div class="empty compact">暂无训练</div>`;
  $("#filterRows").innerHTML = dashboard.filterConfig.map((band) => `
    <tr>
      <td>${band.filter_position}</td><td>${band.wavelength_nm} nm</td><td>${band.bandwidth_nm ?? "--"}</td>
      <td>${band.exposure_ms ?? "--"}</td><td>${band.gain ?? "--"}</td><td>${band.enabled ? badge("Enabled") : badge("Disabled")}</td>
    </tr>
  `).join("");
}

async function loadDatasets() {
  const params = new URLSearchParams({
    query: $("#datasetSearch")?.value.trim() || "",
    fruitType: $("#datasetFruitFilter")?.value.trim() || "",
    variety: $("#datasetVarietyFilter")?.value.trim() || "",
    dirty: $("#datasetDirtyFilter")?.value || "",
    archived: $("#datasetArchivedFilter")?.value || "0",
  });
  const payload = await api(`/api/model-studio/datasets?${params.toString()}`);
  studio.datasets = payload.datasets || [];
  const rows = studio.datasets.map((ds) => `
    <tr data-dataset="${ds.dataset_id}">
      <td><b>${escapeHtml(ds.dataset_name)}</b><br><small>${escapeHtml(ds.dataset_id)}</small></td>
      <td>${escapeHtml(ds.fruit_type || "--")}</td>
      <td>${escapeHtml(ds.variety || "generic")}</td>
      <td>${ds.sample_count || 0}</td>
      <td>${ds.label_count || 0}<br><small>SSC ${ds.labelCompleteness?.ssc || 0} · TA ${ds.labelCompleteness?.ta || 0} · pH ${ds.labelCompleteness?.ph || 0}</small></td>
      <td>${escapeHtml((ds.versions || []).map((v) => v.version_name).join(", ") || "--")}</td>
      <td>${Number(ds.archived || 0) ? badge("Archived") : Number(ds.dirty || 0) ? badge("Dirty") : badge("Clean")}</td>
      <td><small>${escapeHtml(ds.local_path || ds.storage_path)}</small></td>
      <td>
        <div class="row-actions">
          <button type="button" data-dataset-detail="${ds.dataset_id}">查看详情</button>
          <details class="more-menu">
            <summary>更多</summary>
            <div class="more-panel">
              <button type="button" data-dataset-import="${ds.dataset_id}">添加样品</button>
              <button type="button" data-dataset-version="${ds.dataset_id}">创建版本</button>
              <button type="button" data-dataset-detail="${ds.dataset_id}">查看详情</button>
              <span class="menu-separator"></span>
              <button type="button" data-dataset-archive="${ds.dataset_id}" ${Number(ds.archived || 0) ? "disabled" : ""}>归档</button>
              <button type="button" class="danger-menu-item" data-dataset-delete="${ds.dataset_id}">永久删除</button>
            </div>
          </details>
        </div>
      </td>
    </tr>
  `).join("");
  $("#datasetRows").innerHTML = rows || `<tr><td colspan="9" class="empty">暂无数据集</td></tr>`;
  const select = $("#datasetSelect");
  select.innerHTML = studio.datasets.map((ds) => `<option value="${escapeHtml(ds.dataset_id)}">${escapeHtml(ds.dataset_name)}</option>`).join("");
  if (studio.selectedDatasetId && !studio.datasets.some((ds) => ds.dataset_id === studio.selectedDatasetId)) {
    studio.selectedDatasetId = "";
    studio.selectedDatasetVersionId = "";
  }
  if (!studio.selectedDatasetId && studio.datasets[0]) studio.selectedDatasetId = studio.datasets[0].dataset_id;
  select.value = studio.selectedDatasetId;
  document.querySelectorAll("[data-dataset]").forEach((row) => row.addEventListener("click", async (event) => {
    if (event.target.closest("button, details, summary")) return;
    studio.selectedDatasetId = row.dataset.dataset;
    studio.selectedDatasetVersionId = "";
    studio.latestQuality = null;
    if ($("#datasetSelect")) $("#datasetSelect").value = studio.selectedDatasetId;
    setPrepStep(Number(selectedDataset()?.sample_count || 0) > 0 ? "labels" : "import");
    await loadDatasetVersions().catch(() => {});
    await loadSamples().catch(() => {});
    renderDatasetSummary();
  }));
  document.querySelectorAll("[data-dataset-detail]").forEach((button) => button.addEventListener("click", () => {
    studio.selectedDatasetId = button.dataset.datasetDetail;
    closeMenus();
    renderDatasetSummary();
    setPrepStep("quality");
  }));
  document.querySelectorAll("[data-dataset-import]").forEach((button) => button.addEventListener("click", () => {
    studio.selectedDatasetId = button.dataset.datasetImport;
    closeMenus();
    setPrepStep("import");
  }));
  document.querySelectorAll("[data-dataset-version]").forEach((button) => button.addEventListener("click", () => {
    studio.selectedDatasetId = button.dataset.datasetVersion;
    closeMenus();
    setPrepStep("version");
  }));
  document.querySelectorAll("[data-dataset-archive]").forEach((button) => button.addEventListener("click", () => archiveDataset(button.dataset.datasetArchive).catch((error) => toast(error.message))));
  document.querySelectorAll("[data-dataset-delete]").forEach((button) => button.addEventListener("click", () => deleteDatasetPermanently(button.dataset.datasetDelete).catch((error) => toast(error.message))));
  await loadDatasetVersions().catch(() => {});
  renderDatasetSummary();
}

async function archiveDataset(datasetId = studio.selectedDatasetId) {
  if (!datasetId) throw new Error("请先选择 Dataset");
  closeMenus();
  await api("/api/model-studio/datasets/archive", { method: "POST", body: JSON.stringify({ datasetId }) });
  toast("Dataset 已归档，历史版本和模型仍保留");
  if (studio.selectedDatasetId === datasetId && ($("#datasetArchivedFilter")?.value || "0") === "0") {
    studio.selectedDatasetId = "";
    studio.selectedDatasetVersionId = "";
  }
  await refreshAll();
}

async function deleteDatasetPermanently(datasetId = studio.selectedDatasetId) {
  if (!datasetId) throw new Error("请先选择 Dataset");
  closeMenus();
  const payload = await api(`/api/model-studio/datasets/${encodeURIComponent(datasetId)}/references`);
  const refs = payload.references;
  const dataset = refs.dataset;
  const confirmed = await confirmPermanentDelete({
    title: "永久删除 Dataset",
    dangerText: "归档可以保留历史；永久删除不可恢复，并会清理该 Dataset 的受管实验数据。",
    confirmLabel: "Dataset Name",
    confirmValue: dataset.dataset_name,
    disabled: !refs.canDeletePermanently,
    blockReason: refs.blockReason || "",
    summaryHtml: datasetDeleteSummary(refs),
  });
  if (!confirmed) return;
  await api("/api/model-studio/datasets/delete", { method: "POST", body: JSON.stringify({ datasetId, confirm: dataset.dataset_name }) });
  toast("Dataset 已永久删除");
  if (studio.selectedDatasetId === datasetId) {
    studio.selectedDatasetId = "";
    studio.selectedDatasetVersionId = "";
    studio.selectedSampleId = "";
    studio.latestQuality = null;
    switchView("datasets");
  }
  await refreshAll();
}

function datasetDeleteSummary(refs) {
  const dataset = refs.dataset || {};
  const summary = refs.summary || {};
  const blocking = refs.blockingModels || [];
  const modelRows = blocking.map((model) => `
    <tr><td>${escapeHtml(model.display_name || model.model_name)}</td><td>${escapeHtml(model.target || "--")}</td><td>${escapeHtml(model.fruit_type || "--")}</td><td>${escapeHtml(model.variety || "generic")}</td><td>${escapeHtml(isDefaultModel(model) ? "Default" : model.status)}</td></tr>
  `).join("");
  return `
    <dl class="modal-summary">
      <dt>Dataset Name</dt><dd>${escapeHtml(dataset.dataset_name || "--")}</dd>
      <dt>Fruit Type</dt><dd>${escapeHtml(dataset.fruit_type || "--")}</dd>
      <dt>Variety</dt><dd>${escapeHtml(dataset.variety || "generic")}</dd>
      <dt>Samples</dt><dd>${summary.samples || 0}</dd>
      <dt>Versions</dt><dd>${summary.versions || 0}</dd>
      <dt>Experiments</dt><dd>${summary.experiments || 0}</dd>
      <dt>Models</dt><dd>${summary.models || 0}</dd>
    </dl>
    ${blocking.length ? `<div class="dependency-block"><strong>阻止删除的模型</strong><table><thead><tr><th>Model Name</th><th>Target</th><th>Fruit</th><th>Variety</th><th>Status</th></tr></thead><tbody>${modelRows}</tbody></table></div>` : ""}
  `;
}

function confirmPermanentDelete({ title, dangerText, confirmLabel, confirmValue, summaryHtml, disabled = false, blockReason = "" }) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-backdrop";
    overlay.innerHTML = `
      <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirmDeleteTitle">
        <div class="modal-head">
          <h3 id="confirmDeleteTitle">${escapeHtml(title)}</h3>
          <button type="button" class="icon-button" data-cancel-delete title="关闭">×</button>
        </div>
        <p class="danger-copy">${escapeHtml(dangerText)}</p>
        ${blockReason ? `<p class="block-reason">${escapeHtml(blockReason)}</p>` : ""}
        <div class="modal-body">${summaryHtml || ""}</div>
        <label class="block-label">${escapeHtml(confirmLabel)}
          <input id="deleteConfirmInput" autocomplete="off" placeholder="${escapeHtml(confirmValue)}" ${disabled ? "disabled" : ""} />
        </label>
        <div class="modal-actions">
          <button type="button" class="ghost-button" data-cancel-delete>取消</button>
          <button type="button" class="danger-button" data-confirm-delete disabled>永久删除</button>
        </div>
      </section>
    `;
    document.body.appendChild(overlay);
    const input = overlay.querySelector("#deleteConfirmInput");
    const confirmButton = overlay.querySelector("[data-confirm-delete]");
    const cleanup = (value) => {
      overlay.remove();
      resolve(value);
    };
    overlay.querySelectorAll("[data-cancel-delete]").forEach((button) => button.addEventListener("click", () => cleanup(false)));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) cleanup(false);
    });
    input?.addEventListener("input", () => {
      confirmButton.disabled = disabled || input.value !== confirmValue;
    });
    confirmButton.addEventListener("click", () => cleanup(true));
    input?.focus();
  });
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
  if (result.result.conflicts) {
    const choice = window.prompt("发现重复样品。输入 skip / replace / new / cancel：", "skip");
    const duplicatePolicy = String(choice || "skip").trim().toLowerCase();
    if (duplicatePolicy === "cancel") return;
    if (["replace", "new"].includes(duplicatePolicy)) {
      result = await api("/api/model-studio/samples/import", { method: "POST", body: JSON.stringify({ datasetId, sourcePath, duplicatePolicy }) });
    }
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
  renderTrainingConfigSummary();
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
  const query = $("#sampleQuery").value.trim();
  const params = new URLSearchParams({
    datasetId,
    query,
    limit: "80",
    includeStatus: $("#sampleIncludeFilter")?.value || "",
    labelStatus: $("#sampleLabelFilter")?.value || "",
  });
  const payload = await api(`/api/model-studio/samples?${params.toString()}`);
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
  const payload = trainingConfigPayload();
  const result = await api("/api/model-studio/experiments", { method: "POST", body: JSON.stringify(payload) });
  studio.selectedExperimentId = result.experiment.experiment_id;
  toast("训练实验已创建");
  await refreshExperiments();
}

async function startTraining() {
  const config = trainingConfigPayload();
  const result = await api("/api/model-studio/training/start", { method: "POST", body: JSON.stringify(config) });
  studio.selectedExperimentId = result.experiment.experiment_id;
  toast("训练任务已启动");
  pollJob(result.job.job_id);
}

function trainingConfigPayload() {
  const datasetId = currentDatasetId();
  studio.selectedDatasetVersionId = $("#datasetVersionSelect")?.value || studio.selectedDatasetVersionId;
  return {
    datasetId,
    datasetVersionId: studio.selectedDatasetVersionId,
    experimentName: $("#experimentName").value.trim(),
    target: $("#targetSelect").value,
    description: $("#experimentDescription").value.trim(),
    models: [...document.querySelectorAll('input[name="modelType"]:checked')].map((item) => item.value),
    preprocessing: [...document.querySelectorAll('input[name="preprocess"]:checked')].map((item) => item.value),
    validationMethod: $("#validationMethod").value,
  };
}

function renderTrainingConfigSummary() {
  const node = $("#trainingConfigSummary");
  if (!node) return;
  try {
    const config = trainingConfigPayload();
    const dataset = selectedDataset();
    const variants = (config.models.length || 0) * (config.preprocessing.length || 0);
    node.textContent = [
      `Dataset: ${dataset?.dataset_name || config.datasetId || "--"}`,
      `Dataset Version: ${config.datasetVersionId || "--"}`,
      `Fruit / Variety: ${dataset?.fruit_type || "--"} / ${dataset?.variety || "generic"}`,
      `Target: ${String(config.target || "").toUpperCase()}`,
      `Algorithms: ${config.models.join(", ") || "--"}`,
      `Preprocessing: ${config.preprocessing.join(", ") || "--"}`,
      `Validation: ${config.validationMethod}`,
      `Expected Model Variants: ${variants}`,
    ].join("\n");
  } catch (_error) {
    node.textContent = "选择 Dataset Version 和训练配置后开始 Run。";
  }
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
  const params = new URLSearchParams({
    query: $("#modelSearch")?.value.trim() || "",
    fruitType: $("#modelFruitFilter")?.value.trim() || "",
    variety: $("#modelVarietyFilter")?.value.trim() || "",
    target: $("#modelTargetFilter")?.value || "",
    status: $("#modelStatusFilter")?.value || "",
  });
  const payload = await api(`/api/model-studio/models?${params.toString()}`);
  $("#modelRows").innerHTML = (payload.models || []).map((model) => {
    const warnings = (model.qualityWarnings || []).map((item) => `<span class="badge warn">${item}</span>`).join("");
    return `
    <article class="registry-card" data-model-card="${model.model_id}">
      <div class="registry-card-head">
        <div>
          <h4>${escapeHtml(model.display_name || model.model_name)}</h4>
          <small title="${escapeHtml(model.model_id)}">${escapeHtml(model.model_id)}</small>
        </div>
        <span>${badge(model.status)} ${model.isDefault ? '<span class="badge good">★ Default</span>' : ""}</span>
      </div>
      <div class="model-line">${escapeHtml(model.fruit_type || "--")} / ${escapeHtml(model.variety || "generic")} · ${escapeHtml(model.target.toUpperCase())}</div>
      <div class="model-line">${escapeHtml(model.model_type)} · ${escapeHtml(model.preprocessing)} · ${escapeHtml(model.dataset_version_label || model.dataset_version_id || "--")}</div>
      <div class="metric-row"><span>R² <b>${fmt(model.r2)}</b></span><span>RMSE <b>${fmt(model.rmse)}</b></span><span>MAE <b>${fmt(model.mae)}</b></span><span>RPD <b>${fmt(model.rpd)}</b></span></div>
      <div class="warning-row">${warnings}</div>
      <div class="card-actions">
        <button type="button" data-detail="${model.model_id}">查看</button>
        <button type="button" data-retrain="${model.model_id}">重训</button>
        <details class="more-menu">
          <summary>更多</summary>
          <div class="more-panel">${modelMoreActions(model)}</div>
        </details>
      </div>
    </article>`;
  }).join("") || `<div class="empty">暂无模型</div>`;
  document.querySelectorAll("[data-detail]").forEach((button) => {
    button.addEventListener("click", async () => showModelDetail(button.dataset.detail));
  });
  document.querySelectorAll("[data-retrain]").forEach((button) => {
    button.addEventListener("click", async () => {
      const payload = await api("/api/model-studio/experiments/retrain", { method: "POST", body: JSON.stringify({ modelId: button.dataset.retrain, datasetVersionId: $("#datasetVersionSelect")?.value || "" }) });
      studio.selectedExperimentId = payload.experiment.experiment_id;
      switchView("experiments");
      toast("已创建重训实验，旧模型未被覆盖");
    });
  });
  document.querySelectorAll("[data-model-action]").forEach((button) => {
    button.addEventListener("click", () => runModelAction(button.dataset.modelAction, button.dataset.modelId).catch((error) => toast(error.message)));
  });
}

function modelMoreActions(model) {
  const actions = [];
  if (model.status === "Candidate") {
    actions.push(menuButton(model, "validate", "Validate"));
    actions.push(menuButton(model, "delete", "Delete Permanently", "danger-menu-item"));
  } else if (model.status === "Validated") {
    actions.push(menuButton(model, "publish", "Publish"));
    actions.push(menuButton(model, "archive", "Archive"));
    actions.push(menuButton(model, "delete", "Delete Permanently", "danger-menu-item"));
  } else if (model.status === "Published" || model.status === "Production") {
    actions.push(menuButton(model, "default", "Set Default"));
    actions.push(menuButton(model, "export", "Export"));
    actions.push(menuButton(model, "archive", "Archive"));
    actions.push('<span class="menu-separator"></span>');
    actions.push(menuButton(model, "delete", "Delete Permanently", "danger-menu-item"));
  } else if (isDefaultModel(model)) {
    actions.push(menuButton(model, "export", "Export"));
    actions.push(menuButton(model, "archive", "Archive", "", true, "当前模型是默认模型，请先将另一个兼容模型设为默认。"));
    actions.push('<span class="menu-separator"></span>');
    actions.push(menuButton(model, "delete", "Delete Permanently", "danger-menu-item", true, "当前模型是默认模型，请先将另一个兼容模型设为默认。"));
  } else if (model.status === "Archived") {
    actions.push(menuButton(model, "export", "Export"));
    actions.push(menuButton(model, "delete", "Delete Permanently", "danger-menu-item"));
  } else {
    actions.push(menuButton(model, "archive", "Archive"));
    actions.push(menuButton(model, "delete", "Delete Permanently", "danger-menu-item"));
  }
  return actions.join("");
}

function menuButton(model, action, label, className = "", disabled = false, title = "") {
  return `<button type="button" class="${className}" data-model-action="${action}" data-model-id="${escapeHtml(model.model_id)}" ${disabled ? "disabled" : ""} title="${escapeHtml(title)}">${label}</button>`;
}

async function runModelAction(action, modelId) {
  closeMenus();
  if (action === "validate") {
    await api("/api/model-studio/models/validate", { method: "POST", body: JSON.stringify(publishPayload(modelId)) });
    toast("模型已标记为 Validated");
  }
  if (action === "publish") {
    await api("/api/model-studio/models/publish", { method: "POST", body: JSON.stringify(publishPayload(modelId)) });
    toast($("#publishAsDefault")?.checked ? "模型已发布并设为默认" : "模型已发布");
  }
  if (action === "default") {
    await api("/api/model-studio/models/default", { method: "POST", body: JSON.stringify({ modelId }) });
    toast("默认模型已更新");
  }
  if (action === "export") {
    const payload = await api("/api/model-studio/models/export", { method: "POST", body: JSON.stringify({ modelId }) });
    toast(payload.bundle.bundlePath);
  }
  if (action === "archive") {
    await api("/api/model-studio/models/archive", { method: "POST", body: JSON.stringify({ modelId }) });
    toast("模型已归档，文件保留");
  }
  if (action === "delete") {
    await deleteModelPermanently(modelId);
  }
  await refreshAll();
}

async function deleteModelPermanently(modelId) {
  const payload = await api(`/api/model-studio/models/${encodeURIComponent(modelId)}`);
  const model = payload.model;
  const defaultBlocked = isDefaultModel(model);
  const warning = isPublishedModel(model)
    ? "删除后该模型将不再出现在检测工作站。永久删除不可恢复。"
    : "永久删除会移除数据库记录和当前模型拥有的受管模型文件，不可恢复。";
  const confirmed = await confirmPermanentDelete({
    title: "永久删除 Model",
    dangerText: defaultBlocked ? "当前模型是默认模型，请先将另一个兼容模型设为默认。" : warning,
    confirmLabel: "Model ID",
    confirmValue: model.model_id,
    disabled: defaultBlocked,
    blockReason: defaultBlocked ? "当前模型是默认模型，请先将另一个兼容模型设为默认。" : "",
    summaryHtml: `
      <dl class="modal-summary">
        <dt>Model Name</dt><dd>${escapeHtml(model.display_name || model.model_name)}</dd>
        <dt>Target</dt><dd>${escapeHtml(model.target || "--")}</dd>
        <dt>Fruit</dt><dd>${escapeHtml(model.fruit_type || "--")}</dd>
        <dt>Variety</dt><dd>${escapeHtml(model.variety || "generic")}</dd>
        <dt>Status</dt><dd>${escapeHtml(isDefaultModel(model) ? "Default" : model.status)}</dd>
        <dt>Model Dir</dt><dd>${escapeHtml(model.fileStatus?.modelDir || model.model_dir || "--")}</dd>
      </dl>
    `,
  });
  if (!confirmed) return;
  await api("/api/model-studio/models/delete", { method: "POST", body: JSON.stringify({ modelId, confirm: model.model_id }) });
  toast("模型已永久删除");
}

async function showModelDetail(modelId) {
  const payload = await api(`/api/model-studio/models/${encodeURIComponent(modelId)}`);
  const model = payload.model;
  const lineage = model.lineage || {};
  window.alert([
    `${model.display_name || model.model_name}`,
    `model_id: ${model.model_id}`,
    `${model.fruit_type || "--"} / ${model.variety || "generic"} / ${model.target.toUpperCase()}`,
    `${model.model_type} · ${model.preprocessing} · ${model.version}`,
    `Dataset Version: ${lineage.datasetVersionLabel || lineage.datasetVersionId || "--"}`,
    `Experiment: ${lineage.experimentId || "--"}`,
    `Run: ${lineage.runId || "--"}`,
    `Files: model=${model.fileStatus?.modelJoblib ? "ok" : "missing"}, metadata=${model.fileStatus?.metadataJson ? "ok" : "missing"}`,
  ].join("\n"));
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
  $("#archiveDataset")?.addEventListener("click", () => archiveDataset().catch((error) => toast(error.message)));
  $("#deleteDatasetPermanently")?.addEventListener("click", () => deleteDatasetPermanently().catch((error) => toast(error.message)));
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
  ["datasetSearch", "datasetFruitFilter", "datasetVarietyFilter", "datasetDirtyFilter", "datasetArchivedFilter"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", () => loadDatasets().catch((error) => toast(error.message)));
    document.getElementById(id)?.addEventListener("change", () => loadDatasets().catch((error) => toast(error.message)));
  });
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
  $("#datasetVersionSelect").addEventListener("change", () => { studio.selectedDatasetVersionId = $("#datasetVersionSelect").value; renderTrainingConfigSummary(); });
  $("#loadSamples").addEventListener("click", () => loadSamples().catch((error) => toast(error.message)));
  ["sampleQuery", "sampleIncludeFilter", "sampleLabelFilter"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", () => loadSamples().catch((error) => toast(error.message)));
    document.getElementById(id)?.addEventListener("change", () => loadSamples().catch((error) => toast(error.message)));
  });
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
  ["experimentName", "targetSelect", "validationMethod", "experimentDescription"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", renderTrainingConfigSummary);
    document.getElementById(id)?.addEventListener("change", renderTrainingConfigSummary);
  });
  document.querySelectorAll('input[name="modelType"], input[name="preprocess"]').forEach((item) => item.addEventListener("change", renderTrainingConfigSummary));
  ["modelSearch", "modelFruitFilter", "modelVarietyFilter", "modelTargetFilter", "modelStatusFilter"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", () => loadModels().catch((error) => toast(error.message)));
    document.getElementById(id)?.addEventListener("change", () => loadModels().catch((error) => toast(error.message)));
  });
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
  renderTrainingConfigSummary();
  setPrepStep(studio.datasets.length ? (Number(selectedDataset()?.sample_count || 0) > 0 ? "labels" : "import") : "create");
});

window.addEventListener("beforeunload", (event) => {
  if (!studio.labelDirty) return;
  event.preventDefault();
  event.returnValue = "";
});
