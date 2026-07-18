const state = {
  document: null,
  lines: [],
  includeHmf: false,
  uploadId: null,
  transportMode: "auto",
  modifiedFields: new Set(),
  validationErrors: [],
  recalcTimer: null,
};

const els = {
  uploadForm: document.querySelector("#upload-form"),
  fileInput: document.querySelector("#pdf-file"),
  fileName: document.querySelector("#file-name"),
  parseButton: document.querySelector("#parse-button"),
  excelForm: document.querySelector("#excel-form"),
  excelPdfInput: document.querySelector("#excel-pdf-file"),
  excelPdfName: document.querySelector("#excel-pdf-name"),
  excelInput: document.querySelector("#excel-file"),
  excelName: document.querySelector("#excel-name"),
  excelTransportMode: document.querySelector("#excel-transport-mode"),
  generateExcelButton: document.querySelector("#generate-excel-button"),
  includeHmf: document.querySelector("#include-hmf"),
  modeButtons: Array.from(document.querySelectorAll(".mode-button")),
  recalculateButton: document.querySelector("#recalculate-button"),
  generatePdfButton: document.querySelector("#generate-pdf-button"),
  downloadJsonButton: document.querySelector("#download-json-button"),
  statusText: document.querySelector("#status-text"),
  warningList: document.querySelector("#warning-list"),
  entryNumber: document.querySelector("#entry-number"),
  lineCount: document.querySelector("#line-count"),
  enteredTotal: document.querySelector("#entered-total"),
  dutyTotal: document.querySelector("#duty-total"),
  otherTotal: document.querySelector("#other-total"),
  grandTotal: document.querySelector("#grand-total"),
  sourceFile: document.querySelector("#source-file"),
  fieldGrid: document.querySelector("#field-grid"),
  tableNote: document.querySelector("#table-note"),
  lineTableBody: document.querySelector("#line-table-body"),
};

const summaryFields = [
  ["entry_type", "Entry Type"],
  ["summary_date", "Summary Date"],
  ["port_code", "Port"],
  ["entry_date", "Entry Date"],
  ["mode_of_transport", "Mode"],
  ["country_of_origin", "Origin"],
  ["import_date", "Import Date"],
  ["bl_or_awb_number", "B/L or AWB"],
  ["manufacturer_id", "Manufacturer"],
  ["exporting_country", "Exporting"],
  ["invoice_number", "Invoice"],
  ["page_size", "Page Size"],
];

function text(value) {
  return value === null || value === undefined || value === "" ? "-" : String(value);
}

function money(value) {
  return text(value);
}

function setBusy(isBusy, label = "处理中") {
  els.parseButton.disabled = isBusy;
  els.generateExcelButton.disabled = isBusy;
  els.recalculateButton.disabled = isBusy || !state.document;
  els.generatePdfButton.disabled = isBusy || !state.document || state.validationErrors.length > 0;
  els.downloadJsonButton.disabled = isBusy || !state.document;
  if (isBusy) {
    els.statusText.textContent = label;
  }
}

function setStatus(message, warnings = []) {
  els.statusText.textContent = message;
  els.warningList.innerHTML = warnings.map((item) => `<div>${escapeHtml(item)}</div>`).join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function applyPayload(payload) {
  state.document = payload.document;
  state.lines = payload.lines || [];
  state.includeHmf = Boolean(payload.include_hmf);
  state.uploadId = payload.upload_id || state.uploadId;
  state.transportMode = payload.transport_mode || state.transportMode || "auto";
  if (payload.modified_fields !== undefined) {
    state.modifiedFields = new Set(payload.modified_fields || []);
  }
  state.validationErrors = payload.validation_errors || [];
  els.includeHmf.checked = state.includeHmf;
  renderModeButtons();
  render();
}

function render() {
  const doc = state.document || {};
  els.entryNumber.textContent = text(doc.entry_number);
  els.lineCount.textContent = String(state.lines.length);
  els.enteredTotal.textContent = money(doc.total_entered_value);
  els.dutyTotal.textContent = money(doc.calculated_duty_total || doc.duty_total);
  els.otherTotal.textContent = money(doc.calculated_other_total || doc.other_total);
  els.grandTotal.textContent = money(doc.calculated_grand_total || doc.grand_total);
  els.sourceFile.textContent = text(doc.source_file);
  els.tableNote.textContent = `${state.lines.length} rows`;

  els.fieldGrid.innerHTML = summaryFields
    .map(([key, label]) => {
      return `<div class="field"><span>${label}</span><strong>${escapeHtml(text(doc[key]))}</strong></div>`;
    })
    .join("");

  renderLines();
  const warnings = collectWarnings();
  setStatus(state.document ? "已解析" : "待上传", warnings);
  setBusy(false);
}

function collectWarnings() {
  if (!state.document) {
    return [];
  }
  const warnings = [];
  warnings.push(...state.validationErrors);
  if (!state.document.has_text_layer) {
    warnings.push("未检测到稳定文本层");
  }
  if (state.document.parse_notes) {
    warnings.push(state.document.parse_notes);
  }
  for (const field of ["duty_variance", "other_variance", "grand_total_variance"]) {
    const value = state.document[field];
    if (value && Number(value) !== 0) {
      warnings.push(`${field}: ${value}`);
    }
  }
  for (const line of state.lines) {
    if (line.parse_notes) {
      warnings.push(`Line ${line.line_no}: ${line.parse_notes}`);
    }
    if (line.hts_description) {
      const suggested = String(line.hts_additional_codes || "").split(";").map((item) => item.trim()).filter(Boolean);
      const currentCodes = String(line.chapter_99_codes || "").split(";").map((item) => item.trim()).filter(Boolean);
      const missing = suggested.filter((code) => !currentCodes.includes(code));
      const possiblyStale = currentCodes.filter((code) => code !== "9903.03.01" && !suggested.includes(code));
      if (missing.length) {
        warnings.push(`Line ${line.line_no}: HTS 提示附加税项 ${missing.join(", ")}，请人工确认`);
      }
      if (possiblyStale.length) {
        warnings.push(`Line ${line.line_no}: 原附加税项 ${possiblyStale.join(", ")} 可能不再匹配新 HTS`);
      }
    }
  }
  return warnings;
}

function renderLines() {
  if (!state.lines.length) {
    els.lineTableBody.innerHTML = '<tr class="empty-row"><td colspan="10">暂无数据</td></tr>';
    return;
  }

  els.lineTableBody.innerHTML = state.lines
    .map((line, index) => {
      const variance = line.duty_variance || line.mpf_variance || line.hmf_variance || "0.00";
      const varianceClass = Number(variance) === 0 ? "variance-ok" : "variance-warn";
      const requiredUnits = text(line.required_units || line.net_unit);
      const htsDescription = line.hts_description
        ? `<small class="official-description" title="${escapeHtml(line.hts_description)}">USITC: ${escapeHtml(line.hts_description)}</small>`
        : "";
      const additionalRates = line.chapter_99_codes
        ? `<small class="additional-rates">${escapeHtml(line.chapter_99_codes)} · ${escapeHtml(text(line.chapter_99_rates))}</small>`
        : "";
      return `
        <tr>
          <td>${escapeHtml(text(line.line_no))}</td>
          <td><div class="description-stack"><span>${escapeHtml(text(line.description))}</span>${htsDescription}</div></td>
          <td><input class="hts-input" data-index="${index}" data-field="hts" aria-label="Line ${escapeHtml(text(line.line_no))} HTS" value="${escapeHtml(text(line.hts))}" /></td>
          <td><div class="quantity-editor"><input class="quantity-input" inputmode="decimal" data-index="${index}" data-field="net_quantity" aria-label="Line ${escapeHtml(text(line.line_no))} net quantity in ${escapeHtml(requiredUnits)}" value="${escapeHtml(text(line.net_quantity))}" /><span class="unit-badge" title="HTS required units">${escapeHtml(requiredUnits)}</span></div></td>
          <td><input class="money-input" inputmode="decimal" data-index="${index}" data-field="entered_value" aria-label="Line ${escapeHtml(text(line.line_no))} entered value in whole US dollars" value="${escapeHtml(text(line.entered_value))}" /></td>
          <td><div class="rate-stack"><input class="rate-input" data-index="${index}" data-field="rate" value="${escapeHtml(text(line.rate))}" />${additionalRates}</div></td>
          <td class="money">${escapeHtml(money(line.calculated_duty_total || line.duty_amount))}</td>
          <td class="money">${escapeHtml(money(line.calculated_mpf_amount || line.mpf_amount))}</td>
          <td class="money">${escapeHtml(money(line.calculated_hmf_amount || line.hmf_amount))}</td>
          <td class="${varianceClass}">${escapeHtml(text(variance))}</td>
        </tr>
      `;
    })
    .join("");
}

async function lookupHts(index) {
  const line = state.lines[index];
  const code = String(line?.hts || "").trim();
  if (!code) {
    return;
  }
  setStatus(`正在查询 HTS ${code}`, collectWarnings());
  try {
    const response = await fetch(`/api/hts-lookup?code=${encodeURIComponent(code)}`);
    if (!response.ok) {
      throw new Error(await errorText(response));
    }
    const result = await response.json();
    line.hts = result.code;
    line.hts_description = result.description;
    line.required_units = result.required_units || line.net_unit;
    if (result.units?.length) {
      const nextUnit = result.units[0];
      if (line.net_unit && line.net_unit !== nextUnit) {
        state.modifiedFields.add(`line:${line.page}:${line.line_no}:net_quantity`);
      }
      line.net_unit = nextUnit;
    }
    if (result.general_rate) {
      line.rate = result.general_rate;
      state.modifiedFields.add(`line:${line.page}:${line.line_no}:rate`);
    }
    line.hts_additional_codes = (result.additional_hts_codes || []).join("; ") || null;
    renderLines();
    await recalculate();
  } catch (error) {
    setStatus(error.message || "HTS 查询失败", collectWarnings());
  }
}

function scheduleRecalculate() {
  window.clearTimeout(state.recalcTimer);
  state.recalcTimer = window.setTimeout(() => {
    recalculate();
  }, 280);
}

function setTransportMode(mode) {
  if (state.transportMode !== mode) {
    state.modifiedFields.add("document:transport_mode");
  }
  state.transportMode = mode;
  if (mode === "ocean") {
    state.includeHmf = true;
  }
  if (mode === "air") {
    state.includeHmf = false;
  }
  els.includeHmf.checked = state.includeHmf;
  renderModeButtons();
  scheduleRecalculate();
}

function renderModeButtons() {
  for (const button of els.modeButtons) {
    button.classList.toggle("active", button.dataset.mode === state.transportMode);
  }
}

async function parseUpload(event) {
  event.preventDefault();
  const file = els.fileInput.files[0];
  if (!file) {
    setStatus("请选择 PDF");
    return;
  }

  setBusy(true, "解析中");
  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await fetch("/api/parse", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      throw new Error(await errorText(response));
    }
    applyPayload(await response.json());
  } catch (error) {
    setBusy(false);
    setStatus(error.message || "解析失败");
  }
}

async function generateFromExcel(event) {
  event.preventDefault();
  const pdfFile = els.excelPdfInput.files[0];
  const excelFile = els.excelInput.files[0];
  if (!pdfFile || !excelFile) {
    setStatus("请选择原始 PDF 和 Excel 文件");
    return;
  }

  setBusy(true, "按 Excel 表2生成新税单中");
  const formData = new FormData();
  formData.append("pdf_file", pdfFile);
  formData.append("excel_file", excelFile);
  formData.append("transport_mode", els.excelTransportMode.value);
  try {
    const response = await fetch("/api/generate-from-excel", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      throw new Error(await errorText(response));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = responseFileName(response) || excelDownloadName(pdfFile.name);
    link.click();
    URL.revokeObjectURL(url);

    const sheetName = response.headers.get("X-Excel-Sheet") || "表2";
    const matchedLines = response.headers.get("X-Matched-Lines") || "-";
    const modifiedFields = response.headers.get("X-Modified-Fields") || "-";
    const transportMode = response.headers.get("X-Transport-Mode") || els.excelTransportMode.value;
    const includeHmf = response.headers.get("X-Include-HMF") === "true" ? "含 HMF" : "不含 HMF";
    setStatus(`新税单已生成：${sheetName}，${transportMode}/${includeHmf}，匹配 ${matchedLines} 行，修改 ${modifiedFields} 个字段`);
  } catch (error) {
    setStatus(error.message || "Excel 自动生成失败");
  } finally {
    setBusy(false);
  }
}

async function recalculate() {
  if (!state.document) {
    return;
  }
  setBusy(true, "计算中");
  try {
    const response = await fetch("/api/recalculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document: state.document,
        lines: state.lines,
        include_hmf: state.includeHmf,
        modified_fields: Array.from(state.modifiedFields),
      }),
    });
    if (!response.ok) {
      throw new Error(await errorText(response));
    }
    applyPayload(await response.json());
  } catch (error) {
    setBusy(false);
    setStatus(error.message || "计算失败");
  }
}

function currentPayload() {
  return {
    document: state.document,
    lines: state.lines,
    include_hmf: state.includeHmf,
    upload_id: state.uploadId,
    transport_mode: state.transportMode,
    modified_fields: Array.from(state.modifiedFields),
  };
}

async function generatePdf() {
  if (!state.document) {
    return;
  }
  window.clearTimeout(state.recalcTimer);
  setBusy(true, "生成 PDF 中");
  try {
    const response = await fetch("/api/generate-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentPayload()),
    });
    if (!response.ok) {
      throw new Error(await errorText(response));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = pdfDownloadName();
    link.click();
    URL.revokeObjectURL(url);
    setStatus("更新税单已生成", collectWarnings());
  } catch (error) {
    setStatus(error.message || "生成失败");
  } finally {
    setBusy(false);
  }
}

async function errorText(response) {
  try {
    const data = await response.json();
    return data.detail || response.statusText;
  } catch {
    return response.statusText;
  }
}

function responseFileName(response) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded) {
    return decodeURIComponent(encoded[1]);
  }
  const quoted = disposition.match(/filename="([^"]+)"/i);
  if (quoted) {
    return quoted[1];
  }
  return null;
}

function downloadJson() {
  if (!state.document) {
    return;
  }
  const blob = new Blob(
    [
      JSON.stringify(
        currentPayload(),
        null,
        2,
      ),
    ],
    { type: "application/json" },
  );
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "7501-adjustment.json";
  link.click();
  URL.revokeObjectURL(url);
}

function pdfDownloadName() {
  const raw = state.document?.source_file || "7501";
  const stem = raw.replace(/\.[^.]+$/, "").replace(/[^\w.-]+/g, "-").replace(/-+/g, "-") || "7501";
  return `${stem}-adjusted-7501.pdf`;
}

function excelDownloadName(pdfName) {
  const stem = pdfName.replace(/\.[^.]+$/, "").replace(/[^\w.-]+/g, "-").replace(/-+/g, "-") || "7501";
  return `${stem}-excel-adjusted.pdf`;
}

els.fileInput.addEventListener("change", () => {
  const file = els.fileInput.files[0];
  els.fileName.textContent = file ? file.name : "选择 PDF";
});

els.uploadForm.addEventListener("submit", parseUpload);
els.excelPdfInput.addEventListener("change", () => {
  const file = els.excelPdfInput.files[0];
  els.excelPdfName.textContent = file ? file.name : "选择原始 7501 PDF";
});
els.excelInput.addEventListener("change", () => {
  const file = els.excelInput.files[0];
  els.excelName.textContent = file ? file.name : "选择 Sample Commercial Invoice Excel";
});
els.excelForm.addEventListener("submit", generateFromExcel);
for (const button of els.modeButtons) {
  button.addEventListener("click", () => setTransportMode(button.dataset.mode));
}
els.includeHmf.addEventListener("change", () => {
  state.modifiedFields.add("document:transport_mode");
  state.includeHmf = els.includeHmf.checked;
  if (state.includeHmf) {
    state.transportMode = "ocean";
  } else if (state.transportMode === "ocean") {
    state.transportMode = "air";
  }
  renderModeButtons();
  scheduleRecalculate();
});
els.recalculateButton.addEventListener("click", recalculate);
els.generatePdfButton.addEventListener("click", generatePdf);
els.downloadJsonButton.addEventListener("click", downloadJson);
els.lineTableBody.addEventListener("input", (event) => {
  const target = event.target;
  if (!target.matches("input[data-index][data-field]")) {
    return;
  }
  const index = Number(target.dataset.index);
  const field = target.dataset.field;
  const line = state.lines[index];
  state.modifiedFields.add(`line:${line.page}:${line.line_no}:${field}`);
  state.lines[index][field] = target.value;
  scheduleRecalculate();
});
els.lineTableBody.addEventListener("change", (event) => {
  const target = event.target;
  if (!target.matches('input[data-field="hts"][data-index]')) {
    return;
  }
  lookupHts(Number(target.dataset.index));
});

render();
