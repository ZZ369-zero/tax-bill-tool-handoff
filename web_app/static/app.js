const state = {
  document: null,
  lines: [],
  includeHmf: false,
  recalcTimer: null,
};

const els = {
  uploadForm: document.querySelector("#upload-form"),
  fileInput: document.querySelector("#pdf-file"),
  fileName: document.querySelector("#file-name"),
  parseButton: document.querySelector("#parse-button"),
  includeHmf: document.querySelector("#include-hmf"),
  recalculateButton: document.querySelector("#recalculate-button"),
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
  els.recalculateButton.disabled = isBusy || !state.document;
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
  els.includeHmf.checked = state.includeHmf;
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
  return warnings;
}

function renderLines() {
  if (!state.lines.length) {
    els.lineTableBody.innerHTML = '<tr class="empty-row"><td colspan="9">暂无数据</td></tr>';
    return;
  }

  els.lineTableBody.innerHTML = state.lines
    .map((line, index) => {
      const variance = line.duty_variance || line.mpf_variance || line.hmf_variance || "0.00";
      const varianceClass = Number(variance) === 0 ? "variance-ok" : "variance-warn";
      return `
        <tr>
          <td>${escapeHtml(text(line.line_no))}</td>
          <td>${escapeHtml(text(line.description))}</td>
          <td>${escapeHtml(text(line.hts))}</td>
          <td><input class="money-input" data-index="${index}" data-field="entered_value" value="${escapeHtml(text(line.entered_value))}" /></td>
          <td><input class="rate-input" data-index="${index}" data-field="rate" value="${escapeHtml(text(line.rate))}" /></td>
          <td class="money">${escapeHtml(money(line.calculated_duty_total || line.duty_amount))}</td>
          <td class="money">${escapeHtml(money(line.calculated_mpf_amount || line.mpf_amount))}</td>
          <td class="money">${escapeHtml(money(line.calculated_hmf_amount || line.hmf_amount))}</td>
          <td class="${varianceClass}">${escapeHtml(text(variance))}</td>
        </tr>
      `;
    })
    .join("");
}

function scheduleRecalculate() {
  window.clearTimeout(state.recalcTimer);
  state.recalcTimer = window.setTimeout(() => {
    recalculate();
  }, 280);
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

async function errorText(response) {
  try {
    const data = await response.json();
    return data.detail || response.statusText;
  } catch {
    return response.statusText;
  }
}

function downloadJson() {
  if (!state.document) {
    return;
  }
  const blob = new Blob(
    [
      JSON.stringify(
        {
          document: state.document,
          lines: state.lines,
          include_hmf: state.includeHmf,
        },
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

els.fileInput.addEventListener("change", () => {
  const file = els.fileInput.files[0];
  els.fileName.textContent = file ? file.name : "选择 PDF";
});

els.uploadForm.addEventListener("submit", parseUpload);
els.includeHmf.addEventListener("change", () => {
  state.includeHmf = els.includeHmf.checked;
  scheduleRecalculate();
});
els.recalculateButton.addEventListener("click", recalculate);
els.downloadJsonButton.addEventListener("click", downloadJson);
els.lineTableBody.addEventListener("input", (event) => {
  const target = event.target;
  if (!target.matches("input[data-index][data-field]")) {
    return;
  }
  const index = Number(target.dataset.index);
  const field = target.dataset.field;
  state.lines[index][field] = target.value;
  scheduleRecalculate();
});

render();
