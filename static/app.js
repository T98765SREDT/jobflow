"use strict";

const state = {
  applications: [],
  analytics: null,
  options: null,
  total: 0,
  page: 1,
  pageSize: 20,
  view: "all",
  searchTimer: null,
  requestSerial: 0,
  selectedId: null,
  pendingImport: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const form = $("#application-form");
const applicationDialog = $("#application-dialog");
const detailsDialog = $("#details-dialog");
const importDialog = $("#import-dialog");

async function api(path, options = {}) {
  if (window.JobFlowDemoApi) return window.JobFlowDemoApi(path, options);
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({ error: "The server returned an invalid response." }));
  if (!response.ok) {
    const error = new Error(data.error || `Request failed with status ${response.status}.`);
    error.fields = data.fields || {};
    error.status = response.status;
    throw error;
  }
  return data;
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function statusClass(status) {
  return String(status || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function parseLocalDate(value) {
  if (!value) return null;
  const parts = String(value).split("-").map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return null;
  const parsed = new Date(parts[0], parts[1] - 1, parts[2]);
  return Number.isNaN(parsed.valueOf()) ? null : parsed;
}

function formatDate(value, includeYear = true) {
  const parsed = parseLocalDate(value);
  if (!parsed) return value || "—";
  return new Intl.DateTimeFormat("en", {
    month: "short", day: "numeric", ...(includeYear ? { year: "numeric" } : {}),
  }).format(parsed);
}

function formatDateTime(value) {
  if (!value) return "—";
  const normalized = /Z|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`;
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.valueOf())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function dueState(value) {
  const target = parseLocalDate(value);
  if (!target) return { className: "", prefix: "", label: "No follow-up" };
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const difference = Math.round((target - today) / 86400000);
  if (difference < 0) return { className: "overdue", prefix: "Overdue", label: `${Math.abs(difference)}d overdue` };
  if (difference === 0) return { className: "today", prefix: "Today", label: "Due today" };
  if (difference <= 7) return { className: "due-soon", prefix: "Due soon", label: `Due in ${difference}d` };
  return { className: "", prefix: "", label: `Due in ${difference}d` };
}

function initials(company = "") {
  const letters = String(company).trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join("");
  return letters.toUpperCase() || "?";
}

function formatSalary(application) {
  if (application.salary_min == null && application.salary_max == null) return "Not specified";
  const currency = application.currency || "USD";
  const formatter = new Intl.NumberFormat("en", {
    style: "currency", currency, maximumFractionDigits: currency === "JPY" ? 0 : 2,
  });
  const minimum = application.salary_min == null ? null : formatter.format(application.salary_min);
  const maximum = application.salary_max == null ? null : formatter.format(application.salary_max);
  const range = minimum && maximum ? `${minimum}–${maximum}` : minimum ? `From ${minimum}` : `Up to ${maximum}`;
  return `${range} / ${(application.salary_period || "Annual").toLowerCase()}`;
}

function optionMarkup(values) {
  return values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
}

function populateOptions() {
  const groups = [
    [$("#status-filter"), state.options.statuses, ""],
    [$("#mode-filter"), state.options.work_modes, ""],
    [form.elements.status, state.options.statuses, "Wishlist"],
    [form.elements.work_mode, state.options.work_modes, "Remote"],
    [form.elements.currency, state.options.currencies, "USD"],
    [form.elements.salary_period, state.options.salary_periods || ["Hourly", "Monthly", "Annual"], "Annual"],
  ];
  for (const [select, values, defaultValue] of groups) {
    const placeholder = select.querySelector("option")?.outerHTML || "";
    select.innerHTML = placeholder + optionMarkup(values);
    if (defaultValue) select.value = defaultValue;
  }
}

function readUrlState() {
  const params = new URLSearchParams(window.location.search);
  state.view = ["all", "active", "follow-up", "interview", "offers"].includes(params.get("view")) ? params.get("view") : "all";
  state.page = Math.max(1, Number.parseInt(params.get("page") || "1", 10) || 1);
  state.pageSize = [10, 20, 50].includes(Number(params.get("limit"))) ? Number(params.get("limit")) : 20;
  $("#search").value = params.get("search") || "";
  $("#status-filter").value = state.options.statuses.includes(params.get("status")) ? params.get("status") : "";
  $("#mode-filter").value = state.options.work_modes.includes(params.get("work_mode")) ? params.get("work_mode") : "";
  const allowedSorts = ["updated_at", "next_action_date", "applied_date", "company", "status"];
  $("#sort").value = allowedSorts.includes(params.get("sort")) ? params.get("sort") : "updated_at";
  $("#page-size").value = String(state.pageSize);
  renderViewTabs();
}

function syncUrl() {
  const params = new URLSearchParams();
  if (new URLSearchParams(window.location.search).has("demo")) params.set("demo", "1");
  const values = {
    search: $("#search").value.trim(),
    status: $("#status-filter").value,
    work_mode: $("#mode-filter").value,
    sort: $("#sort").value === "updated_at" ? "" : $("#sort").value,
    view: state.view === "all" ? "" : state.view,
    page: state.page === 1 ? "" : String(state.page),
    limit: state.pageSize === 20 ? "" : String(state.pageSize),
  };
  Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
  const query = params.toString();
  history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
}

function renderViewTabs() {
  $$("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function renderActiveFilters() {
  const labels = [];
  if (state.view !== "all") {
    const names = { active: "Active", "follow-up": "Needs follow-up", interview: "Interviews", offers: "Offers" };
    labels.push({ key: "view", label: names[state.view] });
  }
  if ($("#search").value.trim()) labels.push({ key: "search", label: `Search: ${$("#search").value.trim()}` });
  if ($("#status-filter").value) labels.push({ key: "status", label: $("#status-filter").value });
  if ($("#mode-filter").value) labels.push({ key: "work_mode", label: $("#mode-filter").value });
  const container = $("#active-filters");
  container.innerHTML = labels.map((filter) => `<button type="button" data-clear="${filter.key}">${escapeHtml(filter.label)}<span aria-hidden="true">×</span><span class="sr-only">Remove filter</span></button>`).join("");
  container.hidden = labels.length === 0;
  $("#clear-filters").hidden = labels.length === 0;
}

function renderApplications() {
  const body = $("#applications-body");
  body.innerHTML = state.applications.map((application) => {
    const due = dueState(application.next_action_date);
    const dateMarkup = application.next_action_date
      ? `<span class="date ${due.className}">${escapeHtml(due.prefix ? `${due.prefix} · ` : "")}${escapeHtml(formatDate(application.next_action_date, false))}</span>`
      : `<span class="date muted">No action</span>`;
    return `
      <tr>
        <td data-label="Opportunity">
          <button class="opportunity table-link" type="button" data-details="${application.id}" aria-label="View ${escapeHtml(application.role)} at ${escapeHtml(application.company)}">
            <span class="company-avatar" aria-hidden="true">${escapeHtml(initials(application.company))}</span>
            <span><strong>${escapeHtml(application.role)}</strong><small>${escapeHtml(application.company)} · ${escapeHtml(application.location || "Location flexible")}</small></span>
          </button>
        </td>
        <td data-label="Status"><span class="status status-${statusClass(application.status)}">${escapeHtml(application.status)}</span></td>
        <td data-label="Work mode"><span class="mode">${escapeHtml(application.work_mode)}</span></td>
        <td data-label="Applied"><span class="date">${escapeHtml(formatDate(application.applied_date, false))}</span></td>
        <td data-label="Next action">${dateMarkup}</td>
        <td data-label="Actions"><button class="button button-quiet button-small" type="button" data-details="${application.id}">View</button></td>
      </tr>`;
  }).join("");
  $("#loading-state").hidden = true;
  $("#empty-state").hidden = state.applications.length !== 0;
  const noun = state.total === 1 ? "application" : "applications";
  $("#result-count").textContent = `${state.total} ${noun}`;
  renderPagination();
  renderActiveFilters();
}

function renderPagination() {
  const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
  const first = state.total ? (state.page - 1) * state.pageSize + 1 : 0;
  const last = Math.min(state.page * state.pageSize, state.total);
  $("#page-summary").textContent = state.total ? `${first}–${last} of ${state.total}` : "0 results";
  $("#previous-page").disabled = state.page <= 1;
  $("#next-page").disabled = state.page >= totalPages;
}

function renderAnalytics() {
  const data = state.analytics;
  $("#stat-total").textContent = data.total;
  $("#stat-active").textContent = data.active;
  $("#stat-interviews").textContent = data.interviews;
  $("#stat-rate").textContent = `${data.response_rate}% response rate from submitted applications`;
  $("#stat-overdue").textContent = data.overdue;
  $("#stat-due-soon").textContent = `${data.due_soon} due in the next 7 days`;

  const max = Math.max(...Object.values(data.by_status), 1);
  $("#stage-chart").innerHTML = state.options.statuses.map((status) => {
    const count = data.by_status[status] || 0;
    const percent = Math.round((count / max) * 100);
    return `<div class="stage-row"><span>${escapeHtml(status)}</span><div class="bar-track" role="img" aria-label="${escapeHtml(status)}: ${count}"><div class="bar-fill status-bar-${statusClass(status)}" style="width:${percent}%"></div></div><strong>${count}</strong></div>`;
  }).join("");

  $("#upcoming-list").innerHTML = data.upcoming.length ? data.upcoming.map((item) => {
    const due = dueState(item.next_action_date);
    const date = parseLocalDate(item.next_action_date);
    return `<button class="upcoming-item" type="button" data-details="${item.id}">
      <span class="date-box ${due.className}"><small>${date ? date.toLocaleString("en", { month: "short" }) : "—"}</small><strong>${date ? date.getDate() : ""}</strong></span>
      <span><strong>${escapeHtml(item.role)}</strong><small>${escapeHtml(item.company)} · ${escapeHtml(due.label)}</small></span>
      <span class="arrow" aria-hidden="true">→</span>
    </button>`;
  }).join("") : `<div class="panel-empty"><strong>Nothing due yet</strong><span>Add a next-action date to build your follow-up queue.</span></div>`;
}

function queryParameters() {
  const params = new URLSearchParams({
    sort: $("#sort").value,
    direction: ["company", "next_action_date", "status"].includes($("#sort").value) ? "asc" : "desc",
    view: state.view,
    page: String(state.page),
    limit: String(state.pageSize),
  });
  const optional = {
    search: $("#search").value.trim(),
    status: $("#status-filter").value,
    work_mode: $("#mode-filter").value,
  };
  Object.entries(optional).forEach(([key, value]) => { if (value) params.set(key, value); });
  return params;
}

function setLoading() {
  $("#loading-state").hidden = false;
  $("#empty-state").hidden = true;
}

function showSystemError(title, message) {
  $("#system-error-title").textContent = title;
  $("#system-error-message").textContent = message;
  $("#system-error").hidden = false;
}

function clearSystemError() {
  $("#system-error").hidden = true;
}

async function loadApplications() {
  const serial = ++state.requestSerial;
  setLoading();
  syncUrl();
  try {
    const result = await api(`/api/applications?${queryParameters()}`);
    if (serial !== state.requestSerial) return false;
    state.applications = result.items;
    state.total = result.total;
    state.page = result.page;
    state.pageSize = result.page_size;
    renderApplications();
    clearSystemError();
    return true;
  } catch (error) {
    if (serial !== state.requestSerial) return false;
    $("#loading-state").hidden = true;
    showSystemError("Applications could not be refreshed.", `${error.message} Your existing data has not been changed.`);
    throw error;
  }
}

async function refreshAnalytics() {
  try {
    state.analytics = await api("/api/analytics");
    renderAnalytics();
  } catch (error) {
    showSystemError("Insights could not be refreshed.", error.message);
    throw error;
  }
}

async function refreshWorkspace() {
  const results = await Promise.allSettled([loadApplications(), refreshAnalytics()]);
  const rejected = results.find((result) => result.status === "rejected");
  if (rejected) throw rejected.reason;
}

function clearFieldErrors() {
  $$("[aria-invalid='true']").forEach((field) => field.removeAttribute("aria-invalid"));
  $("#form-errors").hidden = true;
  $("#form-errors").innerHTML = "";
}

function openForm(application = null) {
  form.reset();
  clearFieldErrors();
  form.elements.status.value = "Wishlist";
  form.elements.work_mode.value = "Remote";
  form.elements.currency.value = "USD";
  form.elements.salary_period.value = "Annual";
  $("#dialog-title").textContent = application ? "Edit application" : "Add application";
  if (application) {
    Object.keys(application).forEach((key) => {
      if (form.elements[key] && application[key] !== null) form.elements[key].value = application[key];
    });
  }
  if (detailsDialog.open) detailsDialog.close();
  applicationDialog.showModal();
  requestAnimationFrame(() => form.elements.company.focus());
}

function closeForm() {
  if (applicationDialog.open) applicationDialog.close();
}

function serializeForm() {
  const values = Object.fromEntries(new FormData(form));
  delete values.id;
  for (const field of ["salary_min", "salary_max"]) values[field] = values[field] === "" ? null : Number(values[field]);
  for (const field of ["applied_date", "next_action_date"]) values[field] = values[field] || null;
  return values;
}

function showFormErrors(error) {
  clearFieldErrors();
  const entries = Object.entries(error.fields || {});
  const messages = entries.map(([field, message]) => `<li><strong>${escapeHtml(field.replaceAll("_", " "))}:</strong> ${escapeHtml(message)}</li>`);
  const box = $("#form-errors");
  box.innerHTML = messages.length ? `<p>Please review the highlighted fields.</p><ul>${messages.join("")}</ul>` : escapeHtml(error.message);
  box.hidden = false;
  entries.forEach(([field]) => form.elements[field]?.setAttribute("aria-invalid", "true"));
  box.focus?.();
}

let toastTimer;
function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast visible${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.className = "toast"; }, 3600);
}

async function submitForm(event) {
  event.preventDefault();
  clearFieldErrors();
  if (!form.reportValidity()) return;
  const id = form.elements.id.value;
  const button = $("#save-application");
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    await api(id ? `/api/applications/${id}` : "/api/applications", {
      method: id ? "PATCH" : "POST", body: JSON.stringify(serializeForm()),
    });
    closeForm();
    showToast(id ? "Application updated." : "Application added.");
    try {
      await refreshWorkspace();
    } catch (_error) {
      showSystemError("The application was saved, but this view did not refresh.", "Use Try again to load the latest data.");
    }
  } catch (error) {
    showFormErrors(error);
  } finally {
    button.disabled = false;
    button.textContent = "Save application";
  }
}

async function findApplication(id) {
  const current = state.applications.find((item) => item.id === id);
  return current || api(`/api/applications/${id}`);
}

function detailItem(label, value, className = "") {
  return `<div class="detail-item ${className}"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value || "—")}</dd></div>`;
}

async function openDetails(id) {
  try {
    const application = await findApplication(id);
    state.selectedId = id;
    $("#details-title").textContent = application.role;
    $("#details-company").textContent = `${application.company}${application.location ? ` · ${application.location}` : ""}`;
    const due = dueState(application.next_action_date);
    $("#details-content").innerHTML = `
      <div class="detail-badges"><span class="status status-${statusClass(application.status)}">${escapeHtml(application.status)}</span><span class="mode-chip">${escapeHtml(application.work_mode)}</span>${application.next_action_date ? `<span class="due-chip ${due.className}">${escapeHtml(due.label)}</span>` : ""}</div>
      <dl class="details-grid">
        ${detailItem("Applied", formatDate(application.applied_date))}
        ${detailItem("Next action", formatDate(application.next_action_date))}
        ${detailItem("Compensation", formatSalary(application), "full")}
        ${detailItem("Source", application.source || "Not specified")}
        ${detailItem("Last updated", formatDateTime(application.updated_at))}
      </dl>
      <section class="detail-notes"><h3>Notes</h3><p>${escapeHtml(application.notes || "No notes have been added.")}</p></section>`;
    const jobLink = $("#open-job-link");
    jobLink.hidden = !application.url;
    jobLink.href = application.url || "#";
    detailsDialog.showModal();
  } catch (error) {
    showToast(error.message, true);
  }
}

function closeDetails() {
  if (detailsDialog.open) detailsDialog.close();
}

async function editSelected() {
  if (!state.selectedId) return;
  try { openForm(await findApplication(state.selectedId)); }
  catch (error) { showToast(error.message, true); }
}

async function deleteApplication(id) {
  const application = await findApplication(id).catch(() => null);
  if (!application || !window.confirm(`Delete the ${application.role} application at ${application.company}? This cannot be undone.`)) return;
  const button = $("#delete-from-details");
  button.disabled = true;
  try {
    await api(`/api/applications/${id}`, { method: "DELETE" });
    closeDetails();
    showToast("Application deleted.");
    const lastItemOnPage = state.applications.length === 1 && state.page > 1;
    if (lastItemOnPage) state.page -= 1;
    try { await refreshWorkspace(); }
    catch (_error) { showSystemError("The application was deleted, but this view did not refresh.", "Use Try again to load the latest data."); }
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function clearFilters(key = null) {
  if (!key || key === "view") state.view = "all";
  if (!key || key === "search") $("#search").value = "";
  if (!key || key === "status") $("#status-filter").value = "";
  if (!key || key === "work_mode") $("#mode-filter").value = "";
  state.page = 1;
  renderViewTabs();
  loadApplications().catch(() => {});
}

function downloadFile(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function csvCell(value) {
  let text = value == null ? "" : String(value);
  if (/^\s*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

async function exportWorkspace(format) {
  try {
    const backup = await api("/api/export");
    const stamp = new Date().toISOString().slice(0, 10);
    if (format === "json") {
      downloadFile(`jobflow-backup-${stamp}.json`, `${JSON.stringify(backup, null, 2)}\n`, "application/json");
      showToast("JSON backup downloaded.");
      return;
    }
    const columns = ["company", "role", "status", "work_mode", "location", "source", "url", "salary_min", "salary_max", "salary_period", "currency", "applied_date", "next_action_date", "notes", "created_at", "updated_at"];
    const rows = [columns.map(csvCell).join(","), ...backup.applications.map((application) => columns.map((column) => csvCell(application[column])).join(","))];
    downloadFile(`jobflow-applications-${stamp}.csv`, `\ufeff${rows.join("\r\n")}\r\n`, "text/csv;charset=utf-8");
    showToast("CSV export downloaded.");
  } catch (error) {
    showToast(`Export failed: ${error.message}`, true);
  }
}

async function prepareImport(file) {
  if (!file) return;
  if (file.size > 1_000_000) {
    showToast("The backup exceeds the 1 MB import limit.", true);
    return;
  }
  try {
    const payload = JSON.parse(await file.text());
    if (!payload || !Array.isArray(payload.applications)) throw new Error("Expected a JobFlow backup with an applications array.");
    if (payload.schema_version && payload.schema_version > 2) throw new Error("This backup was created by a newer JobFlow schema.");
    state.pendingImport = payload;
    $("#import-summary").textContent = `${file.name} contains ${payload.applications.length} ${payload.applications.length === 1 ? "application" : "applications"}.`;
    importDialog.showModal();
  } catch (error) {
    showToast(`Could not read backup: ${error.message}`, true);
  } finally {
    $("#import-file").value = "";
  }
}

async function importWorkspace(event) {
  event.preventDefault();
  if (!state.pendingImport) return;
  const mode = new FormData(event.currentTarget).get("import_mode") || "append";
  if (mode === "replace" && !window.confirm("Replace every application in this workspace with the validated backup?")) return;
  const button = $("#confirm-import");
  button.disabled = true;
  button.textContent = "Importing…";
  try {
    const result = await api(`/api/import?mode=${mode}`, { method: "POST", body: JSON.stringify(state.pendingImport) });
    importDialog.close();
    state.pendingImport = null;
    state.page = 1;
    showToast(`${result.imported} ${result.imported === 1 ? "application" : "applications"} imported.`);
    try { await refreshWorkspace(); }
    catch (_error) { showSystemError("The backup was imported, but this view did not refresh.", "Use Try again to load the latest data."); }
  } catch (error) {
    showToast(`Import failed: ${error.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = "Import backup";
  }
}

function toggleExportMenu(force) {
  const menu = $("#export-menu");
  const trigger = $("#export-trigger");
  const shouldOpen = force ?? menu.hidden;
  menu.hidden = !shouldOpen;
  trigger.setAttribute("aria-expanded", String(shouldOpen));
  if (shouldOpen) menu.querySelector("button")?.focus();
}

function bindDialogBackdrop(dialog, close) {
  dialog.addEventListener("click", (event) => {
    const rect = dialog.getBoundingClientRect();
    const inside = event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
    if (!inside) close();
  });
}

function bindEvents() {
  $("#add-application").addEventListener("click", () => openForm());
  $("#close-dialog").addEventListener("click", closeForm);
  $("#cancel-dialog").addEventListener("click", closeForm);
  form.addEventListener("submit", submitForm);
  form.addEventListener("input", (event) => event.target.removeAttribute?.("aria-invalid"));
  bindDialogBackdrop(applicationDialog, closeForm);

  $("#applications-body").addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-details]");
    if (trigger) openDetails(Number(trigger.dataset.details));
  });
  $("#upcoming-list").addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-details]");
    if (trigger) openDetails(Number(trigger.dataset.details));
  });
  $("#close-details").addEventListener("click", closeDetails);
  $("#edit-from-details").addEventListener("click", editSelected);
  $("#delete-from-details").addEventListener("click", () => deleteApplication(state.selectedId));
  bindDialogBackdrop(detailsDialog, closeDetails);

  $$("[data-view]").forEach((button) => button.addEventListener("click", () => {
    state.view = button.dataset.view;
    state.page = 1;
    renderViewTabs();
    loadApplications().catch(() => {});
  }));
  [$("#status-filter"), $("#mode-filter"), $("#sort")].forEach((element) => element.addEventListener("change", () => {
    state.page = 1;
    loadApplications().catch(() => {});
  }));
  $("#search").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => { state.page = 1; loadApplications().catch(() => {}); }, 250);
  });
  $("#active-filters").addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-clear]");
    if (trigger) clearFilters(trigger.dataset.clear);
  });
  $("#clear-filters").addEventListener("click", () => clearFilters());
  $("#empty-clear").addEventListener("click", () => clearFilters());
  $("#page-size").addEventListener("change", (event) => {
    state.pageSize = Number(event.target.value);
    state.page = 1;
    loadApplications().catch(() => {});
  });
  $("#previous-page").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; loadApplications().catch(() => {}); } });
  $("#next-page").addEventListener("click", () => { state.page += 1; loadApplications().catch(() => {}); });
  $("#retry-load").addEventListener("click", () => refreshWorkspace().catch(() => {}));

  $("#export-trigger").addEventListener("click", (event) => { event.stopPropagation(); toggleExportMenu(); });
  $("#export-menu").addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-export]");
    if (trigger) { toggleExportMenu(false); exportWorkspace(trigger.dataset.export); }
  });
  document.addEventListener("click", (event) => { if (!event.target.closest(".menu-wrap")) toggleExportMenu(false); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("#export-menu").hidden) toggleExportMenu(false); });

  $("#import-trigger").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", (event) => prepareImport(event.target.files[0]));
  $("#import-form").addEventListener("submit", importWorkspace);
  $("#close-import").addEventListener("click", () => importDialog.close());
  $("#cancel-import").addEventListener("click", () => importDialog.close());
  bindDialogBackdrop(importDialog, () => importDialog.close());

  $("#reset-demo").addEventListener("click", async () => {
    if (!window.JobFlowDemoReset || !window.confirm("Reset this browser demo to its sample applications?")) return;
    window.JobFlowDemoReset();
    state.page = 1;
    await refreshWorkspace().catch(() => {});
    showToast("Demo workspace reset.");
  });
  window.addEventListener("popstate", () => { readUrlState(); loadApplications().catch(() => {}); });
}

async function initialize() {
  if (window.JobFlowDemoApi) {
    document.title = "JobFlow — Interactive Portfolio Demo";
    $("#demo-notice").hidden = false;
    $(".workspace-state strong").textContent = "Browser demo";
    $(".workspace-state small").textContent = "Local storage · No account needed";
  }
  bindEvents();
  try {
    state.options = await api("/api/meta/options");
    populateOptions();
    readUrlState();
    await refreshWorkspace();
  } catch (error) {
    $("#loading-state").hidden = true;
    showSystemError("JobFlow could not start.", `${error.message} Check the local server and try again.`);
  }
}

initialize();
