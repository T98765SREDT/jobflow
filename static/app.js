"use strict";

const state = {
  applications: [],
  analytics: null,
  insights: null,
  insightsWindow: "all",
  today: null,
  options: null,
  total: 0,
  page: 1,
  pageSize: 20,
  view: "all",
  displayView: "table",
  searchTimer: null,
  requestSerial: 0,
  selectedId: null,
  workspace: null,
  pendingImport: null,
  pendingCsvImport: null,
  importPreviewSerial: 0,
  pendingConfirmation: null,
  confirmationReturnFocus: null,
  pendingConflictDraft: null,
  applicationsLoaded: false,
  analyticsLoaded: false,
  insightsLoaded: false,
  todayLoaded: false,
};

const IMPORT_MERGE_FIELDS = [
  ["company", "Company"], ["role", "Role"], ["location", "Location"], ["work_mode", "Work mode"],
  ["source", "Source"], ["url", "Job URL"], ["salary_min", "Minimum salary"], ["salary_max", "Maximum salary"],
  ["salary_period", "Salary period"], ["currency", "Currency"], ["applied_date", "Applied date"], ["notes", "Notes"],
];

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const form = $("#application-form");
const applicationDialog = $("#application-dialog");
const detailsDialog = $("#details-dialog");
const importDialog = $("#import-dialog");
const csvDialog = $("#csv-dialog");
const confirmDialog = $("#confirm-dialog");
const demoRecoveryDialog = $("#demo-recovery-dialog");

const DRAFT_STORAGE_KEY = "jobflow.application-draft.v1";
const API_TIMEOUT_MS = 12000;
const MAX_GET_RETRIES = 2;

function makeRequestId() {
  return globalThis.crypto?.randomUUID ? crypto.randomUUID() : `jobflow-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function waitForRetry(delay) {
  return new Promise((resolve) => setTimeout(resolve, delay));
}

function createApiError(data, status, fallback = "The request could not be completed.") {
  const info = data && typeof data.error === "object" && data.error !== null ? data.error : data || {};
  const message = typeof info.message === "string" ? info.message : (typeof data?.error === "string" ? data.error : fallback);
  const error = new Error(message);
  error.fields = info.fields || data?.fields || {};
  error.status = status;
  error.code = info.code || data?.code || "REQUEST_FAILED";
  error.retryable = Boolean(info.retryable ?? data?.retryable);
  error.requestId = info.request_id || data?.request_id || "";
  error.current = data?.current || info.current || null;
  error.conflicts = Array.isArray(data?.conflicts) ? data.conflicts : [];
  error.applicationId = data?.application_id || info.application_id || null;
  error.details = data;
  return error;
}

async function api(path, options = {}) {
  if (window.JobFlowDemoApi) return window.JobFlowDemoApi(path, options);
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const method = String(options.method || "GET").toUpperCase();
  const canRetry = method === "GET" || method === "HEAD";
  const requestId = headers["X-Request-ID"] || makeRequestId();
  let attempt = 0;
  while (true) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
    const requestHeaders = { ...headers, "X-Request-ID": requestId };
    try {
      const response = await fetch(path, { ...options, method, headers: requestHeaders, signal: controller.signal });
      if (response.status === 204) return null;
      const data = await response.json().catch(() => null);
      if (data === null) {
        const invalid = createApiError(null, response.status, "The server returned an invalid response.");
        invalid.code = "INVALID_RESPONSE";
        invalid.requestId = response.headers.get("X-Request-ID") || requestId;
        throw invalid;
      }
      if (!response.ok) {
        const error = createApiError(data, response.status, `Request failed with status ${response.status}.`);
        error.requestId = error.requestId || response.headers.get("X-Request-ID") || requestHeaders["X-Request-ID"];
        const retryableStatus = [408, 425, 429, 502, 503, 504].includes(response.status);
        if (canRetry && attempt < MAX_GET_RETRIES && (error.retryable || retryableStatus)) {
          const retryAfter = Number(response.headers.get("Retry-After"));
          attempt += 1;
          await waitForRetry(Math.min(Number.isFinite(retryAfter) ? retryAfter * 1000 : 250 * (2 ** (attempt - 1)), 2000));
          continue;
        }
        throw error;
      }
      return data;
    } catch (error) {
      const timedOut = error?.name === "AbortError";
      if (canRetry && attempt < MAX_GET_RETRIES && (timedOut || !error?.status)) {
        attempt += 1;
        await waitForRetry(Math.min(250 * (2 ** (attempt - 1)), 2000));
        continue;
      }
      if (timedOut) {
        const timeoutError = new Error("The server took too long to respond. Please try again.");
        timeoutError.code = "REQUEST_TIMEOUT";
        timeoutError.status = 408;
        timeoutError.retryable = true;
        timeoutError.requestId = requestHeaders["X-Request-ID"];
        throw timeoutError;
      }
      if (error instanceof TypeError && !error.status) {
        error.message = "The server is unreachable. Check that JobFlow is running and try again.";
        error.code = "NETWORK_ERROR";
        error.retryable = true;
        error.requestId = requestHeaders["X-Request-ID"];
      }
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }
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

function todayIso() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
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
  state.displayView = ["table", "board"].includes(params.get("display")) ? params.get("display") : "table";
  state.page = Math.max(1, Number.parseInt(params.get("page") || "1", 10) || 1);
  state.pageSize = [10, 20, 50].includes(Number(params.get("limit"))) ? Number(params.get("limit")) : 20;
  $("#search").value = params.get("search") || "";
  $("#status-filter").value = state.options.statuses.includes(params.get("status")) ? params.get("status") : "";
  $("#mode-filter").value = state.options.work_modes.includes(params.get("work_mode")) ? params.get("work_mode") : "";
  const allowedSorts = ["updated_at", "next_action_date", "applied_date", "company", "status"];
  $("#sort").value = allowedSorts.includes(params.get("sort")) ? params.get("sort") : "updated_at";
  $("#page-size").value = String(state.pageSize);
  renderViewTabs();
  renderDisplayTabs();
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
    display: state.displayView === "table" ? "" : state.displayView,
    detail: state.selectedId ? String(state.selectedId) : "",
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

function renderDisplayTabs() {
  $$('[data-display-view]').forEach((button) => {
    const active = button.dataset.displayView === state.displayView;
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

function applicationAge(application) {
  const applied = parseLocalDate(application.applied_date);
  if (!applied) return "Not applied";
  const today = parseLocalDate(todayIso());
  const days = Math.max(0, Math.floor((today - applied) / 86400000));
  return `${days} day${days === 1 ? "" : "s"} in pipeline`;
}

function boardNextTask(application) {
  if (!application.next_action_date) return "No next action";
  const due = dueState(application.next_action_date);
  return `${due.label} · ${formatDate(application.next_action_date, false)}`;
}

function renderBoard() {
  const board = $("#board-wrap");
  if (!board) return;
  if (!state.applications.length) {
    board.innerHTML = `<div class="board-empty"><strong>No applications in this view</strong><span>Clear a filter or add an opportunity to see the pipeline board.</span></div>`;
    return;
  }
  const stages = state.options?.stages || ["Wishlist", "Ready", "Applied", "Interview", "Offer", "Closed"];
  const grouped = Object.fromEntries(stages.map((stage) => [stage, []]));
  state.applications.forEach((application) => {
    const stage = grouped[application.stage] ? application.stage : (grouped[application.status] ? application.status : "Wishlist");
    grouped[stage].push(application);
  });
  const card = (application) => {
    const stage = application.stage || application.status;
    const canMove = stage !== "Closed";
    const moveMarkup = canMove ? `<div class="board-card-move"><label><span class="sr-only">Move ${escapeHtml(application.role)} to another stage</span><select data-board-stage="${application.id}"><option value="">Move to…</option>${stages.filter((option) => option !== stage).map((option) => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`).join("")}</select></label><button class="button button-secondary button-small" type="button" data-board-move="${application.id}" data-board-version="${application.version}">Move</button></div>` : `<span class="board-closed-note">Closed record</span>`;
    return `<article class="board-card">
      <button class="board-card-main" type="button" data-details="${application.id}" aria-label="Open ${escapeHtml(application.role)} at ${escapeHtml(application.company)}">
        <span class="company-avatar" aria-hidden="true">${escapeHtml(initials(application.company))}</span>
        <span class="board-card-copy"><strong>${escapeHtml(application.role)}</strong><small>${escapeHtml(application.company)}</small></span>
      </button>
      <div class="board-card-meta"><span>${escapeHtml(boardNextTask(application))}</span><span>${escapeHtml(applicationAge(application))}</span></div>
      ${moveMarkup}
    </article>`;
  };
  const column = (stage) => `<section class="board-column board-column-${statusClass(stage)}" aria-labelledby="board-${statusClass(stage)}-title">
    <div class="board-column-heading"><h3 id="board-${statusClass(stage)}-title">${escapeHtml(stage)}</h3><span>${grouped[stage].length}</span></div>
    <div class="board-column-list">${grouped[stage].length ? grouped[stage].map(card).join("") : `<div class="board-column-empty">No applications</div>`}</div>
  </section>`;
  const activeColumns = stages.filter((stage) => stage !== "Closed").map(column).join("");
  const closedCount = grouped.Closed?.length || 0;
  const closedColumn = stages.includes("Closed") ? `<details class="board-column board-column-closed"><summary><span>Closed</span><strong>${closedCount}</strong></summary><div class="board-column-list">${closedCount ? grouped.Closed.map(card).join("") : `<div class="board-column-empty">No closed applications</div>`}</div></details>` : "";
  board.innerHTML = `<div class="board-scroll"><div class="board-columns">${activeColumns}${closedColumn}</div></div><p class="board-caption">Showing the current filters and page. Use Move to… for an accessible stage change.</p>`;
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
        <td data-label="Actions"><button class="button button-quiet button-small" type="button" data-details="${application.id}" aria-label="View ${escapeHtml(application.role)} at ${escapeHtml(application.company)}">View</button></td>
      </tr>`;
  }).join("");
  renderBoard();
  const showBoard = state.displayView === "board" && state.applicationsLoaded && state.applications.length > 0;
  $(".table-wrap").hidden = showBoard;
  $("#board-wrap").hidden = !showBoard;
  $(".table-wrap").setAttribute("aria-busy", "false");
  $("#loading-state").hidden = true;
  $("#empty-state").hidden = state.applications.length !== 0 || !state.applicationsLoaded || !state.analyticsLoaded || state.analytics.total === 0;
  const noun = state.total === 1 ? "application" : "applications";
  $("#result-count").textContent = `${state.total} ${noun}`;
  renderPagination();
  renderActiveFilters();
  renderFirstRun();
}

function renderFirstRun() {
  const panel = $("#first-run");
  if (!panel) return;
  const hasLoadedWorkspace = state.applicationsLoaded && state.analyticsLoaded && Boolean(state.analytics);
  const isEmptyWorkspace = hasLoadedWorkspace && state.analytics.total === 0;
  panel.hidden = !isEmptyWorkspace;
  $("#empty-state").hidden = isEmptyWorkspace || state.applications.length !== 0 || !hasLoadedWorkspace;
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

  const attention = data.attention || [];
  const attentionLabels = {
    overdue: "Overdue",
    today: "Due today",
    due_soon: "Due soon",
    missing: "No next action",
  };
  $("#attention-summary").textContent = `${data.attention_total || 0} open item${data.attention_total === 1 ? "" : "s"}`;
  $("#attention-list").innerHTML = attention.length ? attention.map((item) => {
    const type = item.attention_type || "missing";
    const date = item.next_action_date ? formatDate(item.next_action_date, false) : "Add a date";
    return `<button class="attention-item attention-${type}" type="button" data-details="${item.id}">
      <span class="attention-label"><strong>${escapeHtml(attentionLabels[type] || "Needs attention")}</strong><small>${escapeHtml(date)}</small></span>
      <span class="attention-copy"><strong>${escapeHtml(item.role)}</strong><small>${escapeHtml(item.company)} · ${escapeHtml(item.status)}</small></span>
      <span class="arrow" aria-hidden="true">→</span>
    </button>`;
  }).join("") : `<div class="panel-empty"><strong>Queue is clear</strong><span>Every active application has a dated next step within the next seven days.</span></div>`;
  renderFirstRun();
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
  $(".table-wrap").setAttribute("aria-busy", "true");
  $("#loading-state").hidden = false;
  $("#empty-state").hidden = true;
}

function showSystemError(title, message, error = null) {
  $("#system-error-title").textContent = title;
  const requestId = error?.requestId ? ` (Request ID: ${error.requestId})` : "";
  $("#system-error-message").textContent = `${message}${requestId}`;
  $("#system-error-command").hidden = Boolean(window.JobFlowDemoApi);
  $("#system-error").hidden = false;
}

function clearSystemError() {
  $("#system-error").hidden = true;
}

function showRegionError(id, message, error = null, retryAction = null) {
  const node = $(`#${id}`);
  if (!node) return;
  const requestId = error?.requestId ? ` <small>Request ID: ${escapeHtml(error.requestId)}</small>` : "";
  node.innerHTML = `<span>${escapeHtml(message)}</span>${requestId}${retryAction ? ` <button type="button" class="link-button" data-region-retry="${escapeHtml(retryAction)}">Try again</button>` : ""}`;
  node.hidden = false;
}

function clearRegionError(id) {
  const node = $(`#${id}`);
  if (node) { node.hidden = true; node.innerHTML = ""; }
}

function setDetailUrl(id, { push = false } = {}) {
  const params = new URLSearchParams(window.location.search);
  if (id) params.set("detail", String(id));
  else params.delete("detail");
  const query = params.toString();
  const url = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  if (push) history.pushState(null, "", url);
  else history.replaceState(null, "", url);
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
    state.applicationsLoaded = true;
    renderApplications();
    clearSystemError();
    return true;
  } catch (error) {
    if (serial !== state.requestSerial) return false;
    $(".table-wrap").setAttribute("aria-busy", "false");
    $("#loading-state").hidden = true;
    showSystemError("Applications could not be refreshed.", `${error.message} Your existing data has not been changed.`, error);
    throw error;
  }
}

async function refreshAnalytics() {
  try {
    state.analytics = await api("/api/analytics");
    state.analyticsLoaded = true;
    clearRegionError("analytics-error");
    renderAnalytics();
  } catch (error) {
    showRegionError("analytics-error", "Pipeline health could not be refreshed. Existing metrics are unchanged.", error, "analytics");
    throw error;
  }
}

function insightRate(value, denominator) {
  if (value == null || !denominator || denominator < 5) return "";
  return ` · ${Number(value).toFixed(1)}%`;
}

function renderInsights() {
  const data = state.insights;
  if (!data) return;
  const submitted = Number(data.submitted || 0);
  const cohortLabel = data.window === "all" ? "All recorded history" : `Last ${data.window} days`;
  const start = data.cohort_start ? formatDateTime(data.cohort_start) : "no recorded submissions";
  const end = data.cohort_end ? formatDateTime(data.cohort_end) : "—";
  $("#insights-window").value = data.window;
  $("#insights-summary").textContent = submitted
    ? `${cohortLabel} · ${submitted} submitted · recorded cohort ${start} to ${end}`
    : `${cohortLabel} has no recorded submissions yet.`;
  const metrics = [
    ["Submitted", data.submitted, null], ["Responded", data.responded, data.response_rate],
    ["Interviewed", data.interviewed, data.interview_rate], ["Offered", data.offered, data.offer_rate],
    ["Accepted", data.accepted, data.acceptance_rate], ["No response", data.no_response, null],
  ];
  $("#historical-funnel").innerHTML = submitted ? metrics.map(([label, value, rate]) => `<div class="historical-metric"><strong>${Number(value || 0)}</strong><span>${label}</span><small>${rate != null && submitted >= 5 ? `${Number(rate).toFixed(1)}% of submitted` : label === "Submitted" ? "cohort denominator" : "raw count · ${submitted} in cohort"}</small></div>`).join("") : `<div class="historical-empty">Add an application and move it to Applied to start a historical funnel. Wishlist records are intentionally excluded.</div>`;

  const durations = Object.entries(data.median_time_in_stage || {});
  $("#stage-duration").innerHTML = durations.length ? `<div class="duration-list">${durations.map(([stage, days]) => `<div class="duration-row"><span>${escapeHtml(stage)}</span><strong>${days == null ? "No completed interval" : `${Number(days).toFixed(1)} days`}</strong></div>`).join("")}</div>` : `<div class="historical-empty">Completed stage intervals will appear after a recorded transition.</div>`;
  const sources = data.source_conversion || [];
  $("#source-conversion").innerHTML = sources.length ? `<div class="source-table"><table><thead><tr><th>Source</th><th>Submitted</th><th>Responded</th><th>Interviewed</th><th>Offered</th><th>Accepted</th></tr></thead><tbody>${sources.map((source) => `<tr><td><strong>${escapeHtml(source.source)}</strong></td><td>${source.submitted}</td><td>${source.responded}${insightRate(source.response_rate, source.submitted)}</td><td>${source.interviewed}${insightRate(source.interview_rate, source.submitted)}</td><td>${source.offered}${insightRate(source.offer_rate, source.submitted)}</td><td>${source.accepted}${insightRate(source.acceptance_rate, source.submitted)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="historical-empty">Source conversion appears after the first recorded submission.</div>`;
  const quality = data.history_quality || {};
  const limitedInCohort = Number(quality.limited || 0);
  const limitedTotal = Number(quality.limited_total ?? limitedInCohort);
  const qualityNote = limitedTotal ? `${limitedInCohort ? `${limitedInCohort} selected submitted record${limitedInCohort === 1 ? " has" : "s have"}` : `${limitedTotal} record${limitedTotal === 1 ? " has" : "s have"}`} limited history. ` : "";
  const responseTime = data.median_time_to_response == null ? "Response timing is unavailable until a timestamped response is recorded." : `Median time to first response: ${Number(data.median_time_to_response).toFixed(1)} days.`;
  $("#insights-note").textContent = `${qualityNote}${responseTime} Percentages use the selected submitted cohort; small samples show counts without rate emphasis.`;
}

async function refreshInsights() {
  const panel = $("#historical-insights-panel");
  if (panel) panel.setAttribute("aria-busy", "true");
  try {
    state.insights = await api(`/api/insights?window=${encodeURIComponent(state.insightsWindow)}`);
    state.insightsLoaded = true;
    renderInsights();
    clearRegionError("insights-error");
    if (panel) panel.setAttribute("aria-busy", "false");
  } catch (error) {
    if (panel) panel.setAttribute("aria-busy", "false");
    showRegionError("insights-error", "Historical insights could not be refreshed. The last successful view is still shown.", error, "insights");
    throw error;
  }
}

function todayTaskGroups(today) {
  return [
    { key: "overdue", label: "Overdue", description: "Past due and still open", tasks: today?.overdue || [], taskGroup: true },
    { key: "due_today", label: "Due today", description: "The next actions for today", tasks: today?.due_today || [], taskGroup: true },
    { key: "missing_next_step", label: "Missing next step", description: "Active applications that need a concrete action", tasks: today?.missing_next_step || [], applicationGroup: true },
    { key: "waiting", label: "Waiting", description: "No action until the waiting date", tasks: today?.waiting || [], applicationGroup: true },
    { key: "upcoming", label: "Upcoming", description: "Open actions after today", tasks: today?.upcoming || [], taskGroup: true },
  ];
}

function renderToday() {
  const container = $("#today-list");
  if (!container) return;
  const today = state.today;
  if (!today) return;
  container.setAttribute("aria-busy", "false");
  const groups = todayTaskGroups(today);
  const total = groups.reduce((sum, group) => sum + group.tasks.length, 0);
  $("#today-summary").textContent = total ? `${total} item${total === 1 ? "" : "s"} in your action center` : "Nothing urgent";
  container.innerHTML = groups.map((group) => {
    const rows = group.tasks.length ? group.tasks.map((item) => {
      if (group.applicationGroup) {
        const waiting = group.key === "waiting";
        return `<article class="today-item" data-details="${item.id}">
          <div class="today-item-copy"><strong>${escapeHtml(item.role)}</strong><small>${escapeHtml(item.company)} · ${waiting ? `Waiting until ${escapeHtml(formatDate(item.waiting_until, false))}` : "Add a task or follow-up date"}</small></div>
          <div class="today-item-actions"><button class="button button-secondary button-small" type="button" data-details="${item.id}">${waiting ? "Open" : "Add next step"}</button></div>
        </article>`;
      }
      const due = dueState(item.due_date);
      return `<article class="today-item" data-task-id="${item.id}">
        <div class="today-item-copy"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.company)} · ${escapeHtml(item.role)} · ${escapeHtml(due.label)}</small></div>
        <div class="today-item-actions"><button class="button button-secondary button-small" type="button" data-complete-task="${item.id}" data-task-version="${item.version}">Complete</button><button class="button button-quiet button-small" type="button" data-snooze-task="${item.id}" data-task-version="${item.version}" data-snooze-days="3">+3d</button></div>
      </article>`;
    }).join("") : `<div class="today-empty">${group.key === "upcoming" ? "No later actions scheduled." : "Nothing here."}</div>`;
    return `<section class="today-group" aria-labelledby="today-${group.key}-title"><div class="today-group-heading"><h3 id="today-${group.key}-title">${group.label}</h3><span>${group.description}</span></div>${rows}</section>`;
  }).join("");
}

async function refreshToday() {
  const container = $("#today-list");
  if (container && !state.todayLoaded) container.setAttribute("aria-busy", "true");
  try {
    state.today = await api("/api/today");
    state.todayLoaded = true;
    renderToday();
  } catch (error) {
    if (container) {
      container.setAttribute("aria-busy", "false");
      const requestId = error.requestId ? ` Request ID: ${error.requestId}` : "";
      container.innerHTML = `<div class="today-empty">Today could not be loaded. Use Refresh to try again.${escapeHtml(requestId)} <button type="button" class="link-button" data-region-retry="today">Try again</button></div>`;
    }
    throw error;
  }
}

async function completeTodayTask(taskId, version, button) {
  if (button?.disabled) return;
  if (button) { button.disabled = true; button.textContent = "Saving…"; }
  try {
    await api(`/api/tasks/${taskId}/complete`, { method: "POST", body: JSON.stringify({ expected_version: Number(version) }) });
    showToast("Task completed.");
    await Promise.all([refreshToday(), loadApplications(), refreshAnalytics()]);
  } catch (error) {
    if (error.status === 409) {
      showToast("This task changed elsewhere. Today was refreshed.", true);
      await refreshToday().catch(() => {});
    } else {
      showToast(`Could not complete task: ${error.message}`, true);
    }
  } finally {
    if (button) { button.disabled = false; button.textContent = "Complete"; }
  }
}

async function completeWorkspaceTask(taskId, version, button) {
  if (button?.disabled) return;
  if (button) { button.disabled = true; button.textContent = "Saving…"; }
  try {
    await api(`/api/tasks/${taskId}/complete`, { method: "POST", body: JSON.stringify({ expected_version: Number(version) }) });
    await openDetails(state.selectedId);
    await Promise.all([refreshToday(), loadApplications(), refreshAnalytics()]);
    showToast("Task completed.");
  } catch (error) {
    if (error.status === 409) {
      showToast("This task changed elsewhere. The workspace was refreshed.", true);
      await openDetails(state.selectedId).catch(() => {});
    } else {
      showToast(`Could not complete task: ${error.message}`, true);
    }
  } finally {
    if (button) { button.disabled = false; button.textContent = "Complete"; }
  }
}

async function submitTask(event) {
  event.preventDefault();
  if (!state.selectedId) return;
  const taskForm = event.currentTarget;
  const button = taskForm.querySelector("[type='submit']");
  button.disabled = true;
  button.textContent = "Adding…";
  try {
    const payload = Object.fromEntries(new FormData(taskForm));
    await api(`/api/applications/${state.selectedId}/tasks`, { method: "POST", body: JSON.stringify(payload) });
    await openDetails(state.selectedId);
    activateWorkspaceTab("tasks");
    await Promise.all([refreshToday(), loadApplications(), refreshAnalytics()]);
    showToast("Task added.");
  } catch (error) {
    showToast(`Could not add task: ${error.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = "Add task";
  }
}

async function snoozeTodayTask(taskId, version, days, button) {
  if (button?.disabled) return;
  const source = [
    ...(state.today?.overdue || []),
    ...(state.today?.due_today || []),
    ...(state.today?.upcoming || []),
  ].find((task) => task.id === Number(taskId));
  const currentDate = source?.due_date ? parseLocalDate(source.due_date) : new Date();
  const today = parseLocalDate(todayIso());
  if (currentDate < today) currentDate.setTime(today.getTime());
  currentDate.setDate(currentDate.getDate() + Number(days || 3));
  const dueDate = `${currentDate.getFullYear()}-${String(currentDate.getMonth() + 1).padStart(2, "0")}-${String(currentDate.getDate()).padStart(2, "0")}`;
  if (button) { button.disabled = true; button.textContent = "Saving…"; }
  try {
    await api(`/api/tasks/${taskId}/snooze`, { method: "POST", body: JSON.stringify({ due_date: dueDate, expected_version: Number(version) }) });
    showToast(`Task moved to ${formatDate(dueDate, false)}.`);
    await Promise.all([refreshToday(), loadApplications(), refreshAnalytics()]);
  } catch (error) {
    if (error.status === 409) {
      showToast("This task changed elsewhere. Today was refreshed.", true);
      await refreshToday().catch(() => {});
    } else {
      showToast(`Could not snooze task: ${error.message}`, true);
    }
  } finally {
    if (button) { button.disabled = false; button.textContent = "+3d"; }
  }
}

async function moveBoardApplication(applicationId, targetStage, version, button) {
  if (!targetStage || button?.disabled) return;
  const current = state.applications.find((application) => application.id === Number(applicationId));
  if (!current || current.stage === "Closed") return;
  if (targetStage === "Closed") {
    showToast("Open the record to choose a closing outcome.", true);
    openDetails(Number(applicationId));
    return;
  }
  const requestId = globalThis.crypto?.randomUUID ? crypto.randomUUID() : `jobflow-board-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  if (button) { button.disabled = true; button.textContent = "Moving…"; }
  try {
    const result = await api(`/api/applications/${applicationId}/transitions`, {
      method: "POST",
      body: JSON.stringify({ to_stage: targetStage, outcome: null, expected_version: Number(version), request_id: requestId }),
    });
    showToast(result.replayed ? "Stage update already recorded." : `Moved to ${targetStage}.`);
    await refreshWorkspace();
  } catch (error) {
    if (error.status === 409) {
      showToast("This card changed elsewhere. The board was refreshed.", true);
      await refreshWorkspace().catch(() => {});
    } else {
      showToast(`Could not move application: ${error.message}`, true);
    }
  } finally {
    if (button) { button.disabled = false; button.textContent = "Move"; }
  }
}

async function refreshWorkspace() {
  const results = await Promise.allSettled([loadApplications(), refreshAnalytics(), refreshToday(), refreshInsights()]);
  const rejected = results.find((result) => result.status === "rejected");
  if (rejected) throw rejected.reason;
}

function clearFieldErrors() {
  $$("[aria-invalid='true']").forEach((field) => field.removeAttribute("aria-invalid"));
  $("#form-errors").hidden = true;
  $("#form-errors").innerHTML = "";
}

function readDraft() {
  try {
    const raw = sessionStorage.getItem(DRAFT_STORAGE_KEY);
    if (!raw) return null;
    const draft = JSON.parse(raw);
    return draft && typeof draft === "object" && !Array.isArray(draft) ? draft : null;
  } catch (_error) {
    return null;
  }
}

function persistDraft() {
  if (form.elements.id.value) return;
  try {
    sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(serializeForm()));
  } catch (_error) {
    // Draft recovery is best effort when the browser blocks sessionStorage.
  }
}

function clearDraft() {
  try {
    sessionStorage.removeItem(DRAFT_STORAGE_KEY);
  } catch (_error) {
    // Ignore storage cleanup failures; the saved application is still valid.
  }
}

function draftHasSecondaryValues(draft) {
  return ["location", "source", "work_mode", "applied_date", "waiting_until", "salary_min", "salary_max", "notes"]
    .some((field) => draft[field] !== null && draft[field] !== undefined && draft[field] !== "");
}

function openForm(application = null) {
  form.reset();
  clearFieldErrors();
  form.elements.id.value = "";
  form.elements.status.value = "Wishlist";
  form.elements.work_mode.value = "Remote";
  form.elements.currency.value = "USD";
  form.elements.salary_period.value = "Annual";
  $("#more-details").open = false;
  $("#draft-status").hidden = true;
  $("#dialog-title").textContent = application ? "Edit application" : "Add application";
  if (application) {
    Object.keys(application).forEach((key) => {
      if (form.elements[key] && application[key] !== null) form.elements[key].value = application[key];
    });
    $("#more-details").open = draftHasSecondaryValues(application);
  } else {
    const draft = readDraft();
    if (draft) {
      Object.keys(draft).forEach((key) => {
        if (form.elements[key] && draft[key] !== null && draft[key] !== undefined) form.elements[key].value = draft[key];
      });
      $("#more-details").open = draftHasSecondaryValues(draft);
      $("#draft-status").hidden = false;
    }
  }
  if (detailsDialog.open) closeDetails();
  applicationDialog.showModal();
  requestAnimationFrame(() => form.elements.company.focus());
}

function closeForm(clearSavedDraft = true) {
  if (applicationDialog.open) applicationDialog.close();
  if (clearSavedDraft && !form.elements.id.value) {
    clearDraft();
    $("#draft-status").hidden = true;
  }
}

function serializeForm() {
  const values = Object.fromEntries(new FormData(form));
  delete values.id;
  for (const field of ["salary_min", "salary_max"]) values[field] = values[field] === "" ? null : Number(values[field]);
  for (const field of ["applied_date", "next_action_date", "waiting_until"]) values[field] = values[field] || null;
  return values;
}

function showFormErrors(error) {
  clearFieldErrors();
  const entries = Object.entries(error.fields || {});
  const messages = entries.map(([field, message]) => `<li><strong>${escapeHtml(field.replaceAll("_", " "))}:</strong> ${escapeHtml(message)}</li>`);
  const box = $("#form-errors");
  if (error.status === 409 && error.code === "VERSION_CONFLICT") {
    box.innerHTML = `<p>${escapeHtml(error.message)}</p><div class="form-error-actions"><button type="button" class="button button-secondary button-small" data-error-action="review">Review latest</button><button type="button" class="button button-secondary button-small" data-error-action="copy">Keep my changes</button><button type="button" class="text-button" data-error-action="cancel">Cancel</button></div>`;
  } else {
    const requestId = error.requestId ? `<small>Request ID: ${escapeHtml(error.requestId)}</small>` : "";
    box.innerHTML = messages.length ? `<p>Please review the highlighted fields.</p><ul>${messages.join("")}</ul>${requestId}` : `${escapeHtml(error.message)}${requestId}`;
  }
  box.hidden = false;
  entries.forEach(([field]) => form.elements[field]?.setAttribute("aria-invalid", "true"));
  box.focus?.();
}

let toastTimer;
let toastAction = null;
function showToast(message, isError = false, options = {}) {
  const toast = $("#toast");
  $("#toast-message").textContent = message;
  const action = $("#toast-action");
  toastAction = options.onAction || null;
  action.hidden = !toastAction;
  action.textContent = options.actionLabel || "Undo";
  toast.className = `toast visible${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.className = "toast"; toastAction = null; action.hidden = true; }, options.duration || (isError ? 7000 : 3600));
}

function dismissToast() {
  clearTimeout(toastTimer);
  toastAction = null;
  $("#toast-action").hidden = true;
  $("#toast").className = "toast";
}

function runToastAction() {
  const action = toastAction;
  dismissToast();
  if (action) action();
}

async function submitForm(event) {
  event.preventDefault();
  clearFieldErrors();
  if (!form.reportValidity()) return;
  const id = form.elements.id.value;
  const attemptedValues = serializeForm();
  const button = $("#save-application");
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    await api(id ? `/api/applications/${id}` : "/api/applications", {
      method: id ? "PATCH" : "POST", body: JSON.stringify(serializeForm()),
    });
    state.pendingConflictDraft = null;
    clearDraft();
    closeForm();
    showToast(id ? "Application updated." : "Application added.");
    try {
      await refreshWorkspace();
    } catch (error) {
      showSystemError("The application was saved, but this view did not refresh.", "Use Try again to load the latest data.", error);
    }
  } catch (error) {
    if (error.status === 409 && error.code === "VERSION_CONFLICT") state.pendingConflictDraft = attemptedValues;
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

const eventLabels = {
  applied: "Applied",
  status_changed: "Status change",
  interview: "Interview",
  follow_up: "Follow-up",
  note: "Note",
  offer: "Offer",
  rejection: "Rejection",
  custom: "Activity",
};

function renderEvents(events) {
  if (!events.length) return `<div class="timeline-empty"><strong>No activity recorded yet</strong><span>Add an interview, follow-up, or note to keep the record auditable.</span></div>`;
  return `<ol class="timeline">${events.map((event) => `
    <li class="timeline-item">
      <span class="timeline-dot" aria-hidden="true"></span>
      <div class="timeline-copy"><div><strong>${escapeHtml(event.title)}</strong><small>${escapeHtml(eventLabels[event.event_type] || "Activity")} · ${escapeHtml(formatDateTime(event.occurred_at))}${event.origin ? ` · ${escapeHtml(event.origin)}` : ""}</small></div><p>${escapeHtml(event.details || "No details added.")}${event.from_stage && event.to_stage ? ` <span class="timeline-transition">${escapeHtml(event.from_stage)} → ${escapeHtml(event.to_stage)}</span>` : ""}</p></div>
      ${event.origin === "user" ? `<button class="timeline-delete" type="button" data-delete-event="${event.id}" aria-label="Delete ${escapeHtml(event.title)}">×</button>` : `<span class="timeline-lock" title="System activity cannot be deleted" aria-label="System activity cannot be deleted">Protected</span>`}
    </li>`).join("")}</ol>`;
}

function renderTransitionPanel(application) {
  const stages = state.options?.stages || [];
  const canTransition = application.stage !== "Closed";
  return `<section class="transition-panel" aria-labelledby="transition-title">
    <div><h3 id="transition-title">Move application</h3><p>Each move records an immutable timeline event.</p></div>
    <div class="transition-controls">
      <label>Next stage<select id="transition-stage" ${canTransition ? "" : "disabled"}>${stages.map((stage) => `<option value="${escapeHtml(stage)}" ${stage === application.stage ? "selected" : ""}>${escapeHtml(stage)}</option>`).join("")}</select></label>
      <label id="transition-outcome-label" hidden>Outcome<select id="transition-outcome">${(state.options?.outcomes || []).map((outcome) => `<option value="${escapeHtml(outcome)}">${escapeHtml(outcome)}</option>`).join("")}</select></label>
      <button class="button button-primary button-small" type="button" id="submit-transition" ${canTransition ? "" : "disabled"}>Save stage</button>
    </div>
    <p class="transition-conflict" id="transition-conflict" hidden></p>
  </section>`;
}

const taskKindLabels = {
  follow_up: "Follow-up",
  preparation: "Preparation",
  interview: "Interview",
  decision: "Decision",
  custom: "Custom",
};

function renderTaskSection(application, openTasks, completedTasks) {
  const tasks = [...openTasks, ...completedTasks];
  const taskOptions = (state.options?.task_kinds || Object.keys(taskKindLabels)).map((kind) => `<option value="${escapeHtml(kind)}">${escapeHtml(taskKindLabels[kind] || kind)}</option>`).join("");
  const taskMarkup = tasks.length ? tasks.map((task) => `
    <li class="task-row ${task.completed_at ? "task-completed" : ""}">
      <div><strong>${escapeHtml(task.title)}</strong><small>${escapeHtml(taskKindLabels[task.kind] || task.kind)} · ${escapeHtml(formatDate(task.due_date, false))}${task.completed_at ? ` · Completed ${escapeHtml(formatDateTime(task.completed_at))}` : ""}</small></div>
      ${task.completed_at ? `<span class="task-check" aria-label="Completed">✓</span>` : `<button class="button button-secondary button-small" type="button" data-workspace-complete-task="${task.id}" data-task-version="${task.version}">Complete</button>`}
    </li>`).join("") : `<li class="timeline-empty"><strong>No tasks yet</strong><span>Add one concrete action so this application stays moving.</span></li>`;
  const formMarkup = application.stage === "Closed" ? `<p class="muted-help">Closed applications keep their history, but cannot receive new tasks.</p>` : `<form class="task-form" id="task-form">
      <label>Type<select name="kind">${taskOptions}</select></label>
      <label>Task<input name="title" maxlength="200" required placeholder="Prepare a concise project example"></label>
      <label>Due<input name="due_date" type="date" required></label>
      <button class="button button-primary button-small" type="submit">Add task</button>
    </form>`;
  return `<section class="tasks-section" aria-labelledby="tasks-title">
    <div class="activity-heading"><div><h3 id="tasks-title">Tasks</h3><p>${openTasks.length} open · ${completedTasks.length} completed</p></div></div>
    ${formMarkup}
    <ul class="task-list">${taskMarkup}</ul>
  </section>`;
}

const artifactKindLabels = {
  job_description: "Job description", resume: "Resume", cover_letter: "Cover letter",
  portfolio: "Portfolio", assessment: "Assessment", other: "Other",
};

function renderMaterialsSection(application, artifacts, submissions) {
  const kindOptions = (state.options?.artifact_kinds || Object.keys(artifactKindLabels)).map((kind) => `<option value="${escapeHtml(kind)}">${escapeHtml(artifactKindLabels[kind] || kind)}</option>`).join("");
  const rows = artifacts.length ? artifacts.map((artifact) => `
    <li class="material-row">
      <div class="material-copy"><div><strong>${escapeHtml(artifact.label)}</strong>${artifact.version_label ? `<span class="material-version">${escapeHtml(artifact.version_label)}</span>` : ""}</div><small>${escapeHtml(artifactKindLabels[artifact.kind] || artifact.kind)}${artifact.notes ? ` · ${escapeHtml(artifact.notes)}` : ""}</small>${artifact.uri ? `<a class="material-link" href="${escapeHtml(artifact.uri)}" target="_blank" rel="noreferrer">Open link</a>` : "<em>Metadata only · no link added</em>"}</div>
      <div class="material-actions"><button class="link-button" type="button" data-new-artifact-version="${artifact.id}">New version</button><button class="link-button" type="button" data-edit-artifact="${artifact.id}">Edit</button><button class="link-button danger-link" type="button" data-delete-artifact="${artifact.id}">Delete</button></div>
    </li>`).join("") : `<li class="timeline-empty"><strong>No materials recorded</strong><span>Add the versions you actually use so a later submission can be reconstructed.</span></li>`;
  const submissionRows = submissions.length ? submissions.map((submission) => `<li class="submission-row"><div><strong>${escapeHtml(formatDateTime(submission.submitted_at))}</strong><small>${submission.items.length} material${submission.items.length === 1 ? "" : "s"}${submission.notes ? ` · ${escapeHtml(submission.notes)}` : ""}</small><ul>${submission.items.map((item) => `<li>${escapeHtml(item.snapshot_label)}${item.snapshot_version_label ? ` <span>${escapeHtml(item.snapshot_version_label)}</span>` : ""}</li>`).join("")}</ul></div><span class="snapshot-badge">Read-only snapshot</span></li>`).join("") : `<li class="timeline-empty"><strong>No submissions recorded</strong><span>Select the exact material versions above when you submit an application.</span></li>`;
  const submissionChoices = artifacts.length ? artifacts.map((artifact) => `<label class="material-choice"><input type="checkbox" name="artifact_id" value="${artifact.id}"><span><strong>${escapeHtml(artifact.label)}</strong><small>${escapeHtml(artifactKindLabels[artifact.kind] || artifact.kind)}${artifact.version_label ? ` · ${escapeHtml(artifact.version_label)}` : ""}</small></span></label>`).join("") : `<p class="muted-help">Add at least one material before creating a submission snapshot.</p>`;
  return `<section class="materials-section" aria-labelledby="materials-title">
    <div class="activity-heading"><div><h3 id="materials-title">Materials</h3><p>Keep version labels and links; files stay outside JobFlow.</p></div></div>
    <form class="material-form" id="artifact-form">
      <input type="hidden" name="id">
      <div class="material-form-row"><label>Type<select name="kind">${kindOptions}</select></label><label>Version label<input name="version_label" maxlength="80" placeholder="v2 · tailored for Python roles"></label></div>
      <label>Label<input name="label" maxlength="160" required placeholder="Resume — backend focus"></label>
      <label>Link <span class="label-help">optional</span><input name="uri" type="url" maxlength="500" placeholder="https://... (http/https only)"></label>
      <label>Notes <span class="label-help">optional</span><textarea name="notes" maxlength="2000" rows="2" placeholder="What changed in this version?"></textarea></label>
      <div class="material-form-actions"><button class="button button-quiet button-small" type="button" id="cancel-artifact" hidden>Cancel edit</button><button class="button button-primary button-small" type="submit" id="save-artifact">Add material</button></div>
    </form>
    <ul class="material-list">${rows}</ul>
    <div class="submission-history"><div class="activity-heading"><div><h3>Submission history</h3><p>These entries never change when a current material is renamed.</p></div></div>
      <form class="submission-form" id="submission-form"><fieldset><legend>Include material versions</legend><div class="material-choices">${submissionChoices}</div></fieldset><label>Submission note <span class="label-help">optional</span><textarea name="notes" maxlength="2000" rows="2" placeholder="Submitted through the company portal"></textarea></label><button class="button button-secondary button-small" type="submit" ${artifacts.length ? "" : "disabled"}>Create submission snapshot</button></form>
      <ul class="submission-list">${submissionRows}</ul>
    </div>
  </section>`;
}

function resetArtifactEditor() {
  const artifactForm = $("#artifact-form");
  if (!artifactForm) return;
  artifactForm.reset();
  artifactForm.elements.id.value = "";
  $("#save-artifact").textContent = "Add material";
  $("#cancel-artifact").hidden = true;
}

function startArtifactEdit(artifactId) {
  const artifact = state.workspace?.artifacts?.find((item) => item.id === Number(artifactId));
  const artifactForm = $("#artifact-form");
  if (!artifact || !artifactForm) return;
  artifactForm.elements.id.value = artifact.id;
  artifactForm.elements.kind.value = artifact.kind;
  artifactForm.elements.label.value = artifact.label;
  artifactForm.elements.version_label.value = artifact.version_label || "";
  artifactForm.elements.uri.value = artifact.uri || "";
  artifactForm.elements.notes.value = artifact.notes || "";
  $("#save-artifact").textContent = "Save material";
  $("#cancel-artifact").hidden = false;
  artifactForm.elements.label.focus();
}

function startArtifactVersion(artifactId) {
  const artifact = state.workspace?.artifacts?.find((item) => item.id === Number(artifactId));
  const artifactForm = $("#artifact-form");
  if (!artifact || !artifactForm) return;
  resetArtifactEditor();
  artifactForm.elements.kind.value = artifact.kind;
  artifactForm.elements.label.value = artifact.label;
  artifactForm.elements.version_label.value = /^v\d+$/i.test(artifact.version_label || "")
    ? `v${Number(artifact.version_label.slice(1)) + 1}`
    : "";
  artifactForm.elements.uri.value = artifact.uri || "";
  artifactForm.elements.notes.value = artifact.notes || "";
  $("#save-artifact").textContent = "Add version";
  $("#cancel-artifact").hidden = false;
  artifactForm.elements.label.focus();
}

async function submitArtifact(event) {
  event.preventDefault();
  if (!state.selectedId) return;
  const artifactForm = event.currentTarget;
  const button = artifactForm.querySelector("[type='submit']");
  const values = Object.fromEntries(new FormData(artifactForm));
  const id = values.id;
  delete values.id;
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    await api(id ? `/api/artifacts/${id}` : `/api/applications/${state.selectedId}/artifacts`, { method: id ? "PATCH" : "POST", body: JSON.stringify(values) });
    await openDetails(state.selectedId, { updateHistory: false });
    activateWorkspaceTab("materials");
    showToast(id ? "Material updated." : "Material added.");
  } catch (error) {
    showToast(`Could not save material: ${error.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = artifactForm.elements.id.value ? "Save material" : "Add material";
  }
}

async function deleteArtifact(artifactId) {
  const artifact = state.workspace?.artifacts?.find((item) => item.id === Number(artifactId));
  if (!artifact || !state.selectedId) return;
  askConfirmation({
    title: "Delete this material?",
    message: `${artifact.label} will be removed unless it is part of an immutable submission snapshot.`,
    confirmLabel: "Delete material",
    trigger: document.activeElement,
    onConfirm: async () => {
      try {
        await api(`/api/artifacts/${artifact.id}`, { method: "DELETE" });
        await openDetails(state.selectedId, { updateHistory: false });
        activateWorkspaceTab("materials");
        showToast("Material deleted.");
      } catch (error) {
        showToast(`Could not delete material: ${error.message}`, true);
      }
    },
  });
}

async function submitSubmission(event) {
  event.preventDefault();
  if (!state.selectedId) return;
  const submissionForm = event.currentTarget;
  const button = submissionForm.querySelector("[type='submit']");
  const artifactIds = [...submissionForm.querySelectorAll("input[name='artifact_id']:checked")].map((input) => Number(input.value));
  button.disabled = true;
  try {
    await api(`/api/applications/${state.selectedId}/submissions`, { method: "POST", body: JSON.stringify({ artifact_ids: artifactIds, notes: submissionForm.elements.notes.value }) });
    await openDetails(state.selectedId, { updateHistory: false });
    activateWorkspaceTab("materials");
    showToast("Submission snapshot created.");
  } catch (error) {
    showToast(`Could not create snapshot: ${error.message}`, true);
    button.disabled = false;
  }
}

const requirementAssessmentLabels = { met: "Met", partial: "Partial", gap: "Gap", unknown: "Unknown" };
const requirementCategoryLabels = {
  skill: "Skill", experience: "Experience", language: "Language", location: "Location",
  work_authorization: "Work authorization", compensation: "Compensation", other: "Other",
};

function renderRequirementSection(application, requirements, summary) {
  const counts = summary?.counts || {};
  const coverage = summary?.coverage == null ? "No known coverage yet" : `${summary.coverage}% known-weight coverage`;
  const evidencePrompt = summary?.missing_evidence_met ? `<span class="requirement-prompt">${summary.missing_evidence_met} met item${summary.missing_evidence_met === 1 ? "" : "s"} still need evidence</span>` : "";
  const categoryOptions = (state.options?.requirement_categories || Object.keys(requirementCategoryLabels)).map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(requirementCategoryLabels[category] || category)}</option>`).join("");
  const assessmentOptions = (state.options?.requirement_assessments || Object.keys(requirementAssessmentLabels)).map((assessment) => `<option value="${escapeHtml(assessment)}">${escapeHtml(requirementAssessmentLabels[assessment] || assessment)}</option>`).join("");
  const rows = requirements.length ? requirements.map((requirement, index) => {
    const assessment = requirementAssessmentLabels[requirement.assessment] || requirement.assessment;
    const missingEvidence = requirement.assessment === "met" && !String(requirement.evidence || "").trim();
    return `<li class="requirement-row requirement-${escapeHtml(requirement.assessment)}">
      <div class="requirement-copy"><div><strong>${escapeHtml(requirement.criterion)}</strong><span class="requirement-assessment">${escapeHtml(assessment)}</span></div><small>${escapeHtml(requirementCategoryLabels[requirement.category] || requirement.category)} · Weight ${requirement.weight}${requirement.evidence ? ` · ${escapeHtml(requirement.evidence)}` : ""}</small>${missingEvidence ? `<em>Evidence recommended</em>` : ""}</div>
      <div class="requirement-actions"><button class="icon-button requirement-move" type="button" data-reorder-requirement="${requirement.id}" data-reorder-direction="up" ${index === 0 ? "disabled" : ""} aria-label="Move ${escapeHtml(requirement.criterion)} up">↑</button><button class="icon-button requirement-move" type="button" data-reorder-requirement="${requirement.id}" data-reorder-direction="down" ${index === requirements.length - 1 ? "disabled" : ""} aria-label="Move ${escapeHtml(requirement.criterion)} down">↓</button><button class="link-button" type="button" data-edit-requirement="${requirement.id}">Edit</button><button class="link-button danger-link" type="button" data-delete-requirement="${requirement.id}">Delete</button></div>
    </li>`;
  }).join("") : `<li class="timeline-empty"><strong>No requirements recorded</strong><span>Add the role's must-haves and compare them with visible evidence.</span></li>`;
  return `<section class="requirements-section" aria-labelledby="requirements-title">
    <div class="activity-heading"><div><h3 id="requirements-title">Requirements</h3><p>Record what the role needs before investing more time.</p></div><span class="requirements-coverage">${escapeHtml(coverage)}</span></div>
    <div class="requirement-summary" aria-label="Requirement summary"><span><strong>${counts.met || 0}</strong> Met</span><span><strong>${counts.partial || 0}</strong> Partial</span><span><strong>${counts.gap || 0}</strong> Gap</span><span><strong>${counts.unknown || 0}</strong> Unknown</span></div>
    ${evidencePrompt}
    <form class="requirement-form" id="requirement-form">
      <input type="hidden" name="id"><input type="hidden" name="position" value="${requirements.length}">
      <label>Requirement<input name="criterion" maxlength="240" required placeholder="Python API experience"></label>
      <div class="requirement-form-row"><label>Category<select name="category">${categoryOptions}</select></label><label>Assessment<select name="assessment">${assessmentOptions}</select></label><label>Weight<select name="weight"><option value="1">1 · Low</option><option value="2">2</option><option value="3">3 · Medium</option><option value="4">4</option><option value="5">5 · High</option></select></label></div>
      <label>Evidence<textarea name="evidence" maxlength="2000" rows="3" placeholder="Project, course, conversation, or link that supports this assessment"></textarea></label>
      <div class="requirement-form-actions"><button class="button button-quiet button-small" type="button" id="cancel-requirement" hidden>Cancel edit</button><button class="button button-primary button-small" type="submit" id="save-requirement">Add requirement</button></div>
    </form>
    <ul class="requirement-list">${rows}</ul>
  </section>`;
}

function resetRequirementEditor() {
  const requirementForm = $("#requirement-form");
  if (!requirementForm) return;
  requirementForm.reset();
  requirementForm.elements.id.value = "";
  requirementForm.elements.position.value = String(state.workspace?.requirements?.length || 0);
  requirementForm.elements.assessment.value = "unknown";
  requirementForm.elements.weight.value = "1";
  $("#save-requirement").textContent = "Add requirement";
  $("#cancel-requirement").hidden = true;
}

function startRequirementEdit(requirementId) {
  const requirement = state.workspace?.requirements?.find((item) => item.id === Number(requirementId));
  const requirementForm = $("#requirement-form");
  if (!requirement || !requirementForm) return;
  requirementForm.elements.id.value = requirement.id;
  requirementForm.elements.position.value = requirement.position;
  requirementForm.elements.criterion.value = requirement.criterion;
  requirementForm.elements.category.value = requirement.category;
  requirementForm.elements.assessment.value = requirement.assessment;
  requirementForm.elements.weight.value = String(requirement.weight);
  requirementForm.elements.evidence.value = requirement.evidence || "";
  $("#save-requirement").textContent = "Save requirement";
  $("#cancel-requirement").hidden = false;
  requirementForm.elements.criterion.focus();
}

async function submitRequirement(event) {
  event.preventDefault();
  if (!state.selectedId) return;
  const requirementForm = event.currentTarget;
  const button = requirementForm.querySelector("[type='submit']");
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    const values = Object.fromEntries(new FormData(requirementForm));
    const id = values.id;
    delete values.id;
    values.weight = Number(values.weight);
    values.position = Number(values.position);
    await api(id ? `/api/requirements/${id}` : `/api/applications/${state.selectedId}/requirements`, { method: id ? "PATCH" : "POST", body: JSON.stringify(values) });
    await openDetails(state.selectedId, { updateHistory: false });
    activateWorkspaceTab("requirements");
    await Promise.all([refreshToday(), loadApplications(), refreshAnalytics()]);
    showToast(id ? "Requirement updated." : "Requirement added.");
  } catch (error) {
    showToast(`Could not save requirement: ${error.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = requirementForm.elements.id.value ? "Save requirement" : "Add requirement";
  }
}

async function deleteRequirement(requirementId) {
  const requirement = state.workspace?.requirements?.find((item) => item.id === Number(requirementId));
  if (!requirement || !state.selectedId) return;
  askConfirmation({
    title: "Delete this requirement?",
    message: `${requirement.criterion} will be removed from this application workspace.`,
    confirmLabel: "Delete requirement",
    trigger: document.activeElement,
    onConfirm: async () => {
      try {
        await api(`/api/requirements/${requirement.id}`, { method: "DELETE" });
        await openDetails(state.selectedId, { updateHistory: false });
        activateWorkspaceTab("requirements");
        showToast("Requirement deleted.");
      } catch (error) {
        showToast(`Could not delete requirement: ${error.message}`, true);
      }
    },
  });
}

async function reorderRequirement(requirementId, direction, button) {
  if (!state.selectedId || !state.workspace?.requirements?.length) return;
  const requirements = [...state.workspace.requirements].sort((left, right) => left.position - right.position || left.id - right.id);
  const index = requirements.findIndex((item) => item.id === Number(requirementId));
  const nextIndex = direction === "up" ? index - 1 : index + 1;
  if (index < 0 || nextIndex < 0 || nextIndex >= requirements.length) return;
  [requirements[index], requirements[nextIndex]] = [requirements[nextIndex], requirements[index]];
  button.disabled = true;
  try {
    await api(`/api/applications/${state.selectedId}/requirements`, {
      method: "PUT",
      body: JSON.stringify({ ordered_ids: requirements.map((item) => item.id) }),
    });
    await openDetails(state.selectedId, { updateHistory: false });
    activateWorkspaceTab("requirements");
    showToast("Requirement order updated.");
  } catch (error) {
    showToast(`Could not reorder requirements: ${error.message}`, true);
    button.disabled = false;
  }
}

function renderWorkspaceTabs(application, workspace) {
  return `<div class="workspace-tabs" role="tablist" aria-label="Application workspace sections">
    <button type="button" role="tab" class="workspace-tab active" data-workspace-tab="overview" aria-selected="true" aria-controls="workspace-panel-overview">Overview</button>
    <button type="button" role="tab" class="workspace-tab" data-workspace-tab="tasks" aria-selected="false" aria-controls="workspace-panel-tasks">Tasks <span>${workspace.open_tasks.length}</span></button>
    <button type="button" role="tab" class="workspace-tab" data-workspace-tab="materials" aria-selected="false" aria-controls="workspace-panel-materials">Materials <span>${workspace.artifacts.length}</span></button>
    <button type="button" role="tab" class="workspace-tab" data-workspace-tab="requirements" aria-selected="false" aria-controls="workspace-panel-requirements">Requirements <span>${workspace.requirements.length}</span></button>
    <button type="button" role="tab" class="workspace-tab" data-workspace-tab="activity" aria-selected="false" aria-controls="workspace-panel-activity">Activity <span>${workspace.events.length}</span></button>
  </div>
  <div class="workspace-tab-panel active" id="workspace-panel-overview" role="tabpanel" data-workspace-panel="overview">
    <div class="detail-badges"><span class="status status-${statusClass(application.status)}">${escapeHtml(application.status)}</span><span class="mode-chip">${escapeHtml(application.work_mode)}</span>${application.next_action_date ? `<span class="due-chip ${dueState(application.next_action_date).className}">${escapeHtml(dueState(application.next_action_date).label)}</span>` : ""}</div>
    <dl class="details-grid">
      ${detailItem("Applied", formatDate(application.applied_date))}
      ${detailItem("Next action", formatDate(application.next_action_date))}
      ${detailItem("Waiting until", formatDate(application.waiting_until))}
      ${detailItem("Compensation", formatSalary(application), "full")}
      ${detailItem("Source", application.source || "Not specified")}
      ${detailItem("Last updated", formatDateTime(application.updated_at))}
    </dl>
    <section class="detail-notes"><h3>Notes</h3><p>${escapeHtml(application.notes || "No notes have been added.")}</p></section>
    ${renderTransitionPanel(application)}
  </div>
  <div class="workspace-tab-panel" id="workspace-panel-tasks" role="tabpanel" data-workspace-panel="tasks" hidden>${renderTaskSection(application, workspace.open_tasks, workspace.completed_tasks)}</div>
  <div class="workspace-tab-panel" id="workspace-panel-materials" role="tabpanel" data-workspace-panel="materials" hidden>${renderMaterialsSection(application, workspace.artifacts, workspace.submissions)}</div>
  <div class="workspace-tab-panel" id="workspace-panel-requirements" role="tabpanel" data-workspace-panel="requirements" hidden>${renderRequirementSection(application, workspace.requirements, workspace.requirement_summary)}</div>
  <div class="workspace-tab-panel" id="workspace-panel-activity" role="tabpanel" data-workspace-panel="activity" hidden>${renderActivitySection(workspace.events)}</div>`;
}

function activateWorkspaceTab(name) {
  $("#details-content")?.querySelectorAll("[data-workspace-tab]").forEach((tab) => {
    const active = tab.dataset.workspaceTab === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  $("#details-content")?.querySelectorAll("[data-workspace-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.workspacePanel !== name;
  });
}

function updateTransitionOutcomeVisibility() {
  const stage = $("#transition-stage");
  const outcome = $("#transition-outcome-label");
  if (!stage || !outcome) return;
  outcome.hidden = stage.value !== "Closed";
}

async function submitTransition() {
  if (!state.selectedId) return;
  const button = $("#submit-transition");
  const stage = $("#transition-stage");
  if (!button || !stage) return;
  const current = await findApplication(state.selectedId);
  if (stage.value === current.stage) {
    showToast("Choose a different stage.", true);
    return;
  }
  const outcome = stage.value === "Closed" ? $("#transition-outcome")?.value || null : null;
  const requestId = globalThis.crypto?.randomUUID ? crypto.randomUUID() : `jobflow-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const payload = { to_stage: stage.value, outcome, expected_version: current.version, request_id: requestId };
  button.disabled = true;
  try {
    const result = await api(`/api/applications/${state.selectedId}/transitions`, { method: "POST", body: JSON.stringify(payload) });
    await openDetails(state.selectedId);
    showToast(result.replayed ? "Stage update already recorded." : "Stage updated.");
  } catch (error) {
    if (error.status === 409 && error.code === "VERSION_CONFLICT") {
      const conflict = $("#transition-conflict");
      if (conflict) {
        conflict.hidden = false;
        conflict.innerHTML = `This application changed in another tab. <button type="button" class="link-button" id="review-latest">Review latest</button> <button type="button" class="link-button" id="copy-transition">Copy my changes</button> <button type="button" class="link-button" id="cancel-conflict">Cancel</button>`;
      }
      showToast("No changes were overwritten. Review the latest record.", true);
    } else {
      showToast(`Could not update stage: ${error.message}`, true);
    }
  } finally {
    button.disabled = false;
  }
}

function renderActivitySection(events) {
  return `<section class="activity-section" aria-labelledby="activity-title">
    <div class="activity-heading"><div><h3 id="activity-title">Activity timeline</h3><p>Keep decisions and follow-ups attached to this application.</p></div><button class="button button-secondary button-small" type="button" id="add-activity" aria-expanded="false">Add activity</button></div>
    <form class="activity-form" id="activity-form" hidden>
      <label>Type<select name="event_type">${Object.entries(eventLabels).map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select></label>
      <label>Title<input name="title" maxlength="160" required placeholder="Technical interview scheduled"></label>
      <label>Details<textarea name="details" maxlength="4000" rows="3" placeholder="What happened, and what should happen next?"></textarea></label>
      <div class="activity-actions"><button class="button button-secondary button-small" type="button" id="cancel-activity">Cancel</button><button class="button button-primary button-small" type="submit">Save activity</button></div>
    </form>
    ${renderEvents(events)}
  </section>`;
}

async function openDetails(id, { updateHistory = true } = {}) {
  try {
    const workspace = await api(`/api/applications/${id}/workspace`);
    const application = workspace.application;
    state.workspace = workspace;
    state.selectedId = id;
    $("#details-title").textContent = application.role;
    $("#details-company").textContent = `${application.company}${application.location ? ` · ${application.location}` : ""}`;
    $("#details-content").innerHTML = renderWorkspaceTabs(application, workspace);
    const jobLink = $("#open-job-link");
    jobLink.hidden = !application.url;
    jobLink.href = application.url || "#";
    if (!detailsDialog.open) detailsDialog.showModal();
    setDetailUrl(id, { push: updateHistory && new URLSearchParams(window.location.search).get("detail") !== String(id) });
  } catch (error) {
    showToast(error.message, true);
  }
}

async function submitActivity(event) {
  event.preventDefault();
  if (!state.selectedId) return;
  const form = event.currentTarget;
  const submit = form.querySelector("[type='submit']");
  submit.disabled = true;
  try {
    const values = Object.fromEntries(new FormData(form));
    await api(`/api/applications/${state.selectedId}/events`, { method: "POST", body: JSON.stringify(values) });
    await openDetails(state.selectedId);
    showToast("Activity added.");
  } catch (error) {
    showToast(`Could not add activity: ${error.message}`, true);
  } finally {
    submit.disabled = false;
  }
}

async function deleteActivity(eventId) {
  if (!state.selectedId) return;
  try {
    await api(`/api/applications/${state.selectedId}/events/${eventId}`, { method: "DELETE" });
    await openDetails(state.selectedId);
    showToast("Activity deleted.");
  } catch (error) {
    showToast(`Could not delete activity: ${error.message}`, true);
  }
}

function closeDetails() {
  if (detailsDialog.open) detailsDialog.close();
  state.selectedId = null;
  state.workspace = null;
  setDetailUrl(null);
}

async function editSelected() {
  if (!state.selectedId) return;
  try { openForm(await findApplication(state.selectedId)); }
  catch (error) { showToast(error.message, true); }
}

function askConfirmation({ title, message, confirmLabel = "Confirm", onConfirm, trigger = document.activeElement }) {
  state.pendingConfirmation = onConfirm;
  state.confirmationReturnFocus = trigger;
  $("#confirm-title").textContent = title;
  $("#confirm-message").textContent = message;
  $("#accept-confirm").textContent = confirmLabel;
  confirmDialog.showModal();
  $("#cancel-confirm").focus();
}

function closeConfirmation() {
  const returnFocus = state.confirmationReturnFocus;
  state.pendingConfirmation = null;
  state.confirmationReturnFocus = null;
  if (confirmDialog.open) confirmDialog.close();
  if (returnFocus && typeof returnFocus.focus === "function") returnFocus.focus();
}

function acceptConfirmation() {
  const action = state.pendingConfirmation;
  closeConfirmation();
  if (action) action();
}

async function deleteApplication(id, trigger = document.activeElement) {
  const application = await findApplication(id).catch(() => null);
  if (!application) return;
  askConfirmation({
    title: "Delete this application?",
    message: `${application.role} at ${application.company} will be removed from this workspace. This action cannot be undone.`,
    confirmLabel: "Delete application",
    trigger,
    onConfirm: () => performDeleteApplication(application),
  });
}

async function performDeleteApplication(application) {
  const id = application.id;
  const button = $("#delete-from-details");
  button.disabled = true;
  try {
    await api(`/api/applications/${id}`, { method: "DELETE" });
    closeDetails();
    showToast("Application deleted.", false, {
      actionLabel: "Undo",
      duration: 8000,
      onAction: () => restoreDeletedApplication(application),
    });
    const lastItemOnPage = state.applications.length === 1 && state.page > 1;
    if (lastItemOnPage) state.page -= 1;
    try { await refreshWorkspace(); }
    catch (error) { showSystemError("The application was deleted, but this view did not refresh.", "Use Try again to load the latest data.", error); }
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function restoreDeletedApplication(application) {
  const fields = ["company", "role", "location", "work_mode", "status", "source", "url", "salary_min", "salary_max", "salary_period", "currency", "applied_date", "next_action_date", "notes"];
  const payload = Object.fromEntries(fields.map((field) => [field, application[field]]));
  try {
    await api("/api/applications", { method: "POST", body: JSON.stringify(payload) });
    state.page = 1;
    await refreshWorkspace();
    showToast("Application restored.");
  } catch (error) {
    showToast(`Could not restore application: ${error.message}`, true);
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

function setActiveSection(sectionId) {
  $$(".nav-item").forEach((link) => {
    const active = link.getAttribute("href") === `#${sectionId}`;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function setupSectionNavigation() {
  const sections = ["dashboard", "today", "applications", "insights"].map((id) => document.getElementById(id)).filter(Boolean);
  $$(".nav-item").forEach((link) => link.addEventListener("click", () => setActiveSection(link.getAttribute("href").slice(1))));
  if (!("IntersectionObserver" in window)) return;
  const observer = new IntersectionObserver((entries) => {
    const visible = entries
      .filter((entry) => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (visible) setActiveSection(visible.target.id);
  }, { rootMargin: "-18% 0px -58% 0px", threshold: [0.1, 0.35, 0.7] });
  sections.forEach((section) => observer.observe(section));
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

function calendarCell(value) {
  return String(value || "")
    .replaceAll("\\", "\\\\")
    .replaceAll(";", "\\;")
    .replaceAll(",", "\\,")
    .replaceAll(/\r?\n/g, "\\n");
}

function nextCalendarDate(value) {
  const date = parseLocalDate(value);
  if (!date) return "";
  date.setDate(date.getDate() + 1);
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("");
}

function calendarDate(value) {
  const date = parseLocalDate(value);
  if (!date) return "";
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("");
}

function buildCalendar(applications) {
  const events = applications
    .filter((application) => application.next_action_date)
    .map((application) => {
      const description = [
        `Status: ${application.status}`,
        application.work_mode ? `Work mode: ${application.work_mode}` : "",
        application.url ? `Job post: ${application.url}` : "",
        application.notes || "",
      ].filter(Boolean).join("\\n");
      return [
        "BEGIN:VEVENT",
        `UID:jobflow-${application.id}@local`,
        `DTSTART;VALUE=DATE:${calendarDate(application.next_action_date)}`,
        `DTEND;VALUE=DATE:${nextCalendarDate(application.next_action_date)}`,
        `SUMMARY:${calendarCell(`Follow up: ${application.role} at ${application.company}`)}`,
        `DESCRIPTION:${calendarCell(description)}`,
        "END:VEVENT",
      ].join("\r\n");
    });
  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//JobFlow//Follow-up Calendar//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    ...events,
    "END:VCALENDAR",
    "",
  ].join("\r\n");
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
    if (format === "calendar") {
      const dated = backup.applications.filter((application) => application.next_action_date);
      if (!dated.length) {
        showToast("Add a next-action date before exporting a calendar.", true);
        return;
      }
      downloadFile(`jobflow-follow-ups-${stamp}.ics`, buildCalendar(dated), "text/calendar;charset=utf-8");
      showToast(`${dated.length} follow-up${dated.length === 1 ? "" : "s"} exported to your calendar.`);
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
    if (/\.csv$/i.test(file.name) || file.type === "text/csv") {
      const parsed = window.JobFlowCsv?.parse(await file.text());
      if (!parsed) throw new Error("CSV import is unavailable in this browser.");
      state.pendingCsvImport = { fileName: file.name, parsed, mapping: window.JobFlowCsv.inferMapping(parsed.headers), duplicatePreview: null, lastResult: null };
      renderCsvMapping();
      csvDialog.showModal();
      return;
    }
    const payload = JSON.parse(await file.text());
    if (!payload || !Array.isArray(payload.applications)) throw new Error("Expected a JobFlow backup with an applications array.");
    if (payload.schema_version && state.options?.schema_version && payload.schema_version > state.options.schema_version) throw new Error("This backup was created by a newer JobFlow schema.");
    state.pendingImport = { ...payload, duplicatePreview: null };
    $("#import-summary").textContent = `${file.name} contains ${payload.applications.length} ${payload.applications.length === 1 ? "application" : "applications"}.`;
    $("#import-duplicates").innerHTML = "<p class=\"muted\">Checking for duplicate applications…</p>";
    importDialog.showModal();
    updateImportPreview();
  } catch (error) {
    showToast(`Could not read backup: ${error.message}`, true);
  } finally {
    $("#import-file").value = "";
  }
}

function renderCsvMapping() {
  const pending = state.pendingCsvImport;
  if (!pending) return;
  const { fields } = window.JobFlowCsv;
  $("#csv-summary").textContent = `${pending.fileName} · ${pending.parsed.rows.length} data rows · choose which columns JobFlow should use.`;
  const options = [`<option value="">Ignore this field</option>`, ...pending.parsed.headers.map((header, index) => `<option value="${index}">${escapeHtml(header)}</option>`)].join("");
  $("#csv-map-grid").innerHTML = fields.map((field) => `
    <label><span>${escapeHtml(field.label)}${["company", "role"].includes(field.key) ? " *" : ""}</span>
      <select data-csv-field="${escapeHtml(field.key)}" aria-label="CSV column for ${escapeHtml(field.label)}">${options}</select>
    </label>`).join("");
  fields.forEach((field) => {
    const select = $(`[data-csv-field="${field.key}"]`);
    select.value = pending.mapping[field.key] ?? "";
    select.addEventListener("change", () => {
      pending.mapping[field.key] = select.value;
      updateCsvPreview();
    });
  });
  updateCsvPreview();
}

async function previewImportRecords(records) {
  return api("/api/import/preview", {
    method: "POST",
    body: JSON.stringify({ schema_version: state.options?.schema_version || 8, applications: records }),
  });
}

function duplicateReasonLabel(conflict) {
  if (conflict.reason === "canonical_url") return "Same canonical job URL";
  if (conflict.reason === "company_role_location") return "Same company, role, and location";
  return "Duplicate row in this import";
}

function importValue(value) {
  if (value == null || value === "") return "—";
  return String(value);
}

function renderDuplicateConflicts(containerId, preview, records, mappedFields) {
  const container = $(containerId);
  if (!container) return;
  const conflicts = preview?.conflicts || [];
  const invalid = preview?.invalid || [];
  if (!conflicts.length && !invalid.length) {
    container.innerHTML = "";
    return;
  }
  const fieldSet = new Set(mappedFields || []);
  const fieldsForMerge = IMPORT_MERGE_FIELDS.filter(([key]) => fieldSet.has(key));
  const invalidMarkup = invalid.length ? `<div class="import-invalid"><strong>${invalid.length} row${invalid.length === 1 ? "" : "s"} need attention</strong><span>Fix the source file or download the row report before importing.</span></div>` : "";
  const conflictMarkup = conflicts.map((conflict) => {
    const index = Number(conflict.incoming_index);
    const incoming = records[index] || conflict.incoming || {};
    const existing = conflict.existing || {};
    const canMerge = Number.isInteger(conflict.existing_application_id) && conflict.existing_application_id > 0;
    const options = [
      `<option value="">Choose an action…</option>`,
      `<option value="skip">Skip incoming row</option>`,
      `<option value="separate">Keep as separate application</option>`,
      ...(canMerge ? [`<option value="merge">Merge selected fields into existing</option>`] : []),
    ].join("");
    const checks = fieldsForMerge.filter(([key]) => incoming[key] != null && incoming[key] !== "").map(([key, label]) => `
      <label><input type="checkbox" data-merge-field="${escapeHtml(key)}" data-merge-field-for="${index}" checked> ${escapeHtml(label)}</label>`).join("");
    const diff = canMerge ? fieldsForMerge.filter(([key]) => incoming[key] != null && incoming[key] !== "" && String(incoming[key]) !== String(existing[key] ?? "")).map(([key, label]) => `
      <div><span>${escapeHtml(label)}</span><b>${escapeHtml(importValue(existing[key]))}</b><em>→</em><b>${escapeHtml(importValue(incoming[key]))}</b></div>`).join("") : "";
    return `<article class="duplicate-conflict" data-duplicate-conflict="${index}">
      <div class="duplicate-conflict-head"><strong>Row ${Number(conflict.source_index ?? index) + 1}</strong><span>${escapeHtml(duplicateReasonLabel(conflict))}</span></div>
      <div class="duplicate-record"><b>${escapeHtml(importValue(incoming.company))}</b><span>${escapeHtml(importValue(incoming.role))} · ${escapeHtml(importValue(incoming.location))}</span></div>
      <label class="duplicate-action"><span>Decision</span><select data-duplicate-index="${index}">${options}</select></label>
      ${canMerge ? `<div class="duplicate-merge-fields" data-merge-fields-wrap="${index}" hidden><span>Fields to update</span>${checks || "<small>No non-empty mapped fields can be merged.</small>"}</div><div class="duplicate-diff" data-duplicate-diff="${index}" hidden>${diff ? `<span>Preview of changes</span>${diff}` : "<small>No changed values to apply.</small>"}</div>` : ""}
    </article>`;
  }).join("");
  container.innerHTML = `${invalidMarkup}${conflictMarkup}`;
  container.querySelectorAll("[data-duplicate-index]").forEach((select) => {
    select.addEventListener("change", () => {
      const index = select.dataset.duplicateIndex;
      const merge = select.value === "merge";
      container.querySelector(`[data-merge-fields-wrap="${index}"]`)?.toggleAttribute("hidden", !merge);
      container.querySelector(`[data-duplicate-diff="${index}"]`)?.toggleAttribute("hidden", !merge);
      if (containerId === "#csv-duplicates") updateCsvImportButton();
      else updateJsonImportButton();
    });
  });
}

function collectDuplicateDecisions(containerId, preview) {
  const container = $(containerId);
  const conflicts = preview?.conflicts || [];
  if (!container || !conflicts.length) return [];
  const decisions = [];
  for (const conflict of conflicts) {
    const index = Number(conflict.incoming_index);
    const select = container.querySelector(`[data-duplicate-index="${index}"]`);
    const action = select?.value || "";
    if (!action) return null;
    const decision = { incoming_index: index, action };
    if (action === "merge") {
      decision.existing_application_id = Number(conflict.existing_application_id);
      decision.fields = [...container.querySelectorAll(`[data-merge-field-for="${index}"]:checked`)].map((input) => input.dataset.mergeField);
      if (!decision.fields.length) return null;
    }
    decisions.push(decision);
  }
  return decisions;
}

function updateCsvImportButton() {
  const pending = state.pendingCsvImport;
  if (!pending) return;
  const result = pending.lastResult;
  const decisions = collectDuplicateDecisions("#csv-duplicates", pending.duplicatePreview);
  $("#confirm-csv").disabled = !result || !result.records.length || Boolean(result.errors.length) || Boolean(pending.duplicatePreview?.invalid?.length) || decisions === null;
}

function updateJsonImportButton() {
  const pending = state.pendingImport;
  if (!pending) return;
  const decisions = collectDuplicateDecisions("#import-duplicates", pending.duplicatePreview);
  $("#confirm-import").disabled = Boolean(pending.duplicatePreview?.invalid?.length) || decisions === null;
}

async function updateCsvPreview() {
  const pending = state.pendingCsvImport;
  if (!pending) return;
  const serial = ++state.importPreviewSerial;
  const result = window.JobFlowCsv.toBackup(pending.parsed, pending.mapping);
  const importRecords = result.allRecords || result.records;
  pending.lastResult = result;
  pending.duplicatePreview = null;
  const required = ["company", "role"].some((key) => pending.mapping[key] === "");
  const issueText = result.errors.length ? ` · ${result.errors.length} row${result.errors.length === 1 ? "" : "s"} missing required fields` : "";
  const duplicateText = result.duplicates ? ` · ${result.duplicates} duplicate row${result.duplicates === 1 ? "" : "s"} found within the file` : "";
  $("#csv-preview").innerHTML = `<strong>${result.records.length} row${result.records.length === 1 ? "" : "s"} ready</strong><span>${required ? "Map Company and Role before importing." : `${result.totalRows} data rows scanned${duplicateText}${issueText}. Checking the existing workspace before writing.`}</span>`;
  $("#csv-error-report").hidden = !result.errors.length;
  $("#csv-duplicates").innerHTML = "";
  $("#confirm-csv").disabled = true;
  if (required || !importRecords.length || result.errors.length) {
    updateCsvImportButton();
    return;
  }
  try {
    const preview = await previewImportRecords(importRecords);
    if (state.pendingCsvImport !== pending || serial !== state.importPreviewSerial) return;
    pending.duplicatePreview = preview;
    $("#csv-error-report").hidden = !(result.errors.length || preview.invalid?.length);
    renderDuplicateConflicts("#csv-duplicates", preview, preview.valid_records || importRecords, Object.keys(pending.mapping).filter((key) => pending.mapping[key] !== ""));
    $("#csv-preview").querySelector("span").textContent = `${result.totalRows} data rows scanned${duplicateText}${issueText}. ${preview.conflicts.length ? "Choose an action for every duplicate before importing." : "No duplicate conflicts found."}`;
    updateCsvImportButton();
  } catch (error) {
    if (state.pendingCsvImport !== pending || serial !== state.importPreviewSerial) return;
    $("#csv-duplicates").innerHTML = `<p class="import-preview-error">Could not check duplicates: ${escapeHtml(error.message)} Try again after adjusting the mapping.</p>`;
    $("#confirm-csv").disabled = true;
  }
}

async function performCsvImport() {
  const pending = state.pendingCsvImport;
  if (!pending) return;
  const button = $("#confirm-csv");
  const result = window.JobFlowCsv.toBackup(pending.parsed, pending.mapping);
  const importRecords = result.allRecords || result.records;
  if (!importRecords.length || result.errors.length) return;
  const decisions = collectDuplicateDecisions("#csv-duplicates", pending.duplicatePreview);
  if (decisions === null) {
    showToast("Choose Skip, Keep separate, or Merge for every duplicate row.", true);
    return;
  }
  button.disabled = true;
  button.textContent = "Importing…";
  try {
    const payload = { schema_version: state.options?.schema_version || 8, exported_at: new Date().toISOString(), applications: importRecords, duplicate_decisions: decisions };
    const imported = await api("/api/import?mode=append", { method: "POST", body: JSON.stringify(payload) });
    csvDialog.close();
    state.pendingCsvImport = null;
    state.page = 1;
    const csvSummary = [`${imported.imported} ${imported.imported === 1 ? "application" : "applications"} imported`];
    if (imported.merged) csvSummary.push(`${imported.merged} merged`);
    if (imported.skipped) csvSummary.push(`${imported.skipped} skipped`);
    showToast(`${csvSummary.join(", ")} from CSV.`);
    try { await refreshWorkspace(); }
    catch (error) { showSystemError("The CSV was imported, but this view did not refresh.", "Use Try again to load the latest data.", error); }
  } catch (error) {
    if (error.code === "DUPLICATES_FOUND" && error.conflicts.length) {
      pending.duplicatePreview = { conflicts: error.conflicts, invalid: [] };
      renderDuplicateConflicts("#csv-duplicates", pending.duplicatePreview, pending.duplicatePreview.valid_records || importRecords, Object.keys(pending.mapping).filter((key) => pending.mapping[key] !== ""));
    }
    showToast(`CSV import failed: ${error.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = "Import rows";
  }
}

async function updateImportPreview() {
  const pending = state.pendingImport;
  if (!pending || !Array.isArray(pending.applications)) return;
  const serial = ++state.importPreviewSerial;
  $("#confirm-import").disabled = true;
  try {
    const preview = await previewImportRecords(pending.applications);
    if (state.pendingImport !== pending || serial !== state.importPreviewSerial) return;
    pending.duplicatePreview = preview;
    renderDuplicateConflicts("#import-duplicates", preview, preview.valid_records || pending.applications, IMPORT_MERGE_FIELDS.map(([key]) => key));
    $("#import-summary").textContent = `${pending.applications.length} application${pending.applications.length === 1 ? "" : "s"} ready to validate. ${preview.conflicts.length ? "Duplicate decisions are required before append." : "No duplicate conflicts found."}`;
    updateJsonImportButton();
  } catch (error) {
    if (state.pendingImport !== pending || serial !== state.importPreviewSerial) return;
    $("#import-duplicates").innerHTML = `<p class="import-preview-error">Could not check duplicates: ${escapeHtml(error.message)}</p>`;
    $("#confirm-import").disabled = false;
  }
}

async function performImport(mode) {
  if (!state.pendingImport) return;
  const button = $("#confirm-import");
  button.disabled = true;
  button.textContent = "Importing…";
  try {
    const decisions = mode === "append" ? collectDuplicateDecisions("#import-duplicates", state.pendingImport.duplicatePreview) : [];
    if (decisions === null) {
      showToast("Choose an action for every duplicate application.", true);
      return;
    }
    const payload = { ...state.pendingImport };
    delete payload.duplicatePreview;
    payload.duplicate_decisions = decisions;
    const result = await api(`/api/import?mode=${mode}`, { method: "POST", body: JSON.stringify(payload) });
    importDialog.close();
    state.pendingImport = null;
    state.page = 1;
    const importSummary = [`${result.imported} ${result.imported === 1 ? "application" : "applications"} imported`];
    if (result.merged) importSummary.push(`${result.merged} merged`);
    if (result.skipped) importSummary.push(`${result.skipped} skipped`);
    showToast(`${importSummary.join(", ")}.`);
    try { await refreshWorkspace(); }
    catch (error) { showSystemError("The backup was imported, but this view did not refresh.", "Use Try again to load the latest data.", error); }
  } catch (error) {
    if (error.code === "DUPLICATES_FOUND" && error.conflicts.length) {
      state.pendingImport.duplicatePreview = { conflicts: error.conflicts, invalid: [] };
      renderDuplicateConflicts("#import-duplicates", state.pendingImport.duplicatePreview, state.pendingImport.duplicatePreview.valid_records || state.pendingImport.applications, IMPORT_MERGE_FIELDS.map(([key]) => key));
    }
    showToast(`Import failed: ${error.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = "Import backup";
  }
}

function importWorkspace(event) {
  event.preventDefault();
  if (!state.pendingImport) return;
  const mode = new FormData(event.currentTarget).get("import_mode") || "append";
  if (mode === "replace") {
    askConfirmation({
      title: "Replace this workspace?",
      message: "Every current application will be replaced after the backup passes validation. This action cannot be undone.",
      confirmLabel: "Replace applications",
      trigger: $("#confirm-import"),
      onConfirm: () => performImport(mode),
    });
    return;
  }
  performImport(mode);
}

function toggleExportMenu(force) {
  const menu = $("#export-menu");
  const trigger = $("#export-trigger");
  const shouldOpen = force ?? menu.hidden;
  menu.hidden = !shouldOpen;
  trigger.setAttribute("aria-expanded", String(shouldOpen));
  if (shouldOpen) menu.querySelector("button")?.focus();
  else if (document.activeElement && menu.contains(document.activeElement)) trigger.focus();
}

function bindDialogBackdrop(dialog, close) {
  dialog.addEventListener("click", (event) => {
    const rect = dialog.getBoundingClientRect();
    const inside = event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
    if (!inside) close();
  });
}

function closeDemoRecovery() {
  if (demoRecoveryDialog?.open) demoRecoveryDialog.close();
}

async function resetCorruptDemo() {
  if (!window.JobFlowDemoReset) return;
  window.JobFlowDemoReset();
  closeDemoRecovery();
  state.page = 1;
  await refreshWorkspace().catch(() => {});
  showToast("Demo workspace reset.");
}

function openDemoRecoveryIfNeeded() {
  if (window.JobFlowDemoHasCorruptCache && demoRecoveryDialog && !demoRecoveryDialog.open) {
    demoRecoveryDialog.showModal();
  }
}

function bindEvents() {
  $("#add-application").addEventListener("click", () => openForm());
  $("#first-add-application").addEventListener("click", () => openForm());
  $("#first-import").addEventListener("click", () => $("#import-file").click());
  $("#learn-workflow").addEventListener("click", () => { $("#workflow-guide").open = true; });
  $("#close-dialog").addEventListener("click", closeForm);
  $("#cancel-dialog").addEventListener("click", closeForm);
  form.addEventListener("submit", submitForm);
  form.addEventListener("input", (event) => {
    event.target.removeAttribute?.("aria-invalid");
    persistDraft();
  });
  form.addEventListener("change", () => persistDraft());
  bindDialogBackdrop(applicationDialog, closeForm);

  $("#applications-body").addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-details]");
    if (trigger) openDetails(Number(trigger.dataset.details));
  });
  $("#upcoming-list").addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-details]");
    if (trigger) openDetails(Number(trigger.dataset.details));
  });
  $("#attention-list").addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-details]");
    if (trigger) openDetails(Number(trigger.dataset.details));
  });
  $("#today-list")?.addEventListener("click", (event) => {
    const complete = event.target.closest("[data-complete-task]");
    const snooze = event.target.closest("[data-snooze-task]");
    const details = event.target.closest("[data-details]");
    if (complete) {
      event.preventDefault();
      completeTodayTask(complete.dataset.completeTask, complete.dataset.taskVersion, complete);
    } else if (snooze) {
      event.preventDefault();
      snoozeTodayTask(snooze.dataset.snoozeTask, snooze.dataset.taskVersion, snooze.dataset.snoozeDays, snooze);
    } else if (details) {
      openDetails(Number(details.dataset.details));
    }
  });
  $("#board-wrap")?.addEventListener("click", (event) => {
    const move = event.target.closest("[data-board-move]");
    const details = event.target.closest("[data-details]");
    if (move) {
      event.preventDefault();
      const card = move.closest(".board-card");
      const target = card?.querySelector(`[data-board-stage="${move.dataset.boardMove}"]`)?.value || "";
      if (!target) {
        showToast("Choose a destination stage first.", true);
        return;
      }
      moveBoardApplication(move.dataset.boardMove, target, move.dataset.boardVersion, move);
    } else if (details) {
      openDetails(Number(details.dataset.details));
    }
  });
  $("#refresh-today")?.addEventListener("click", () => refreshToday().catch(() => {}));
  $("#close-details").addEventListener("click", closeDetails);
  $("#edit-from-details").addEventListener("click", editSelected);
  $("#delete-from-details").addEventListener("click", (event) => deleteApplication(state.selectedId, event.currentTarget));
  $("#details-content").addEventListener("click", (event) => {
    const workspaceTab = event.target.closest("[data-workspace-tab]");
    const completeTask = event.target.closest("[data-workspace-complete-task]");
    const newArtifactVersion = event.target.closest("[data-new-artifact-version]");
    const editArtifact = event.target.closest("[data-edit-artifact]");
    const deleteArtifactButton = event.target.closest("[data-delete-artifact]");
    const cancelArtifact = event.target.closest("#cancel-artifact");
    const editRequirement = event.target.closest("[data-edit-requirement]");
    const deleteRequirementButton = event.target.closest("[data-delete-requirement]");
    const reorderRequirementButton = event.target.closest("[data-reorder-requirement]");
    const cancelRequirement = event.target.closest("#cancel-requirement");
    const addButton = event.target.closest("#add-activity");
    const cancelButton = event.target.closest("#cancel-activity");
    const deleteButton = event.target.closest("[data-delete-event]");
    const transitionButton = event.target.closest("#submit-transition");
    const reviewLatest = event.target.closest("#review-latest");
    const copyTransition = event.target.closest("#copy-transition");
    const cancelConflict = event.target.closest("#cancel-conflict");
    if (workspaceTab) {
      activateWorkspaceTab(workspaceTab.dataset.workspaceTab);
      return;
    }
    if (completeTask) {
      completeWorkspaceTask(completeTask.dataset.workspaceCompleteTask, completeTask.dataset.taskVersion, completeTask);
      return;
    }
    if (newArtifactVersion) {
      activateWorkspaceTab("materials");
      startArtifactVersion(newArtifactVersion.dataset.newArtifactVersion);
      return;
    }
    if (editArtifact) {
      activateWorkspaceTab("materials");
      startArtifactEdit(editArtifact.dataset.editArtifact);
      return;
    }
    if (deleteArtifactButton) {
      deleteArtifact(deleteArtifactButton.dataset.deleteArtifact);
      return;
    }
    if (cancelArtifact) {
      resetArtifactEditor();
      return;
    }
    if (editRequirement) {
      activateWorkspaceTab("requirements");
      startRequirementEdit(editRequirement.dataset.editRequirement);
      return;
    }
    if (deleteRequirementButton) {
      deleteRequirement(deleteRequirementButton.dataset.deleteRequirement);
      return;
    }
    if (reorderRequirementButton) {
      reorderRequirement(reorderRequirementButton.dataset.reorderRequirement, reorderRequirementButton.dataset.reorderDirection, reorderRequirementButton);
      return;
    }
    if (cancelRequirement) {
      resetRequirementEditor();
      return;
    }
    if (addButton) {
      const activityForm = $("#activity-form");
      const open = activityForm.hidden;
      activityForm.hidden = !open;
      addButton.setAttribute("aria-expanded", String(open));
      if (open) activityForm.elements.title.focus();
    }
    if (cancelButton) {
      $("#activity-form").hidden = true;
      $("#add-activity").setAttribute("aria-expanded", "false");
    }
    if (deleteButton) {
      askConfirmation({
        title: "Delete this activity?",
        message: "This timeline entry will be removed from the application record.",
        confirmLabel: "Delete activity",
        trigger: deleteButton,
        onConfirm: () => deleteActivity(deleteButton.dataset.deleteEvent),
      });
    }
    if (transitionButton) submitTransition();
    if (reviewLatest) openDetails(state.selectedId);
    if (copyTransition) {
      const stage = $("#transition-stage")?.value || "";
      const outcome = stage === "Closed" ? $("#transition-outcome")?.value || null : null;
      navigator.clipboard?.writeText(JSON.stringify({ to_stage: stage, outcome })).then(() => showToast("Your proposed change was copied."));
    }
    if (cancelConflict) {
      const conflict = $("#transition-conflict");
      if (conflict) { conflict.hidden = true; conflict.innerHTML = ""; }
    }
  });
  $("#form-errors").addEventListener("click", async (event) => {
    const action = event.target.closest("[data-error-action]")?.dataset.errorAction;
    if (!action) return;
    if (action === "cancel") {
      state.pendingConflictDraft = null;
      closeForm();
      return;
    }
    if (action === "review") {
      const latest = await findApplication(Number(form.elements.id.value)).catch(() => null);
      state.pendingConflictDraft = null;
      if (latest) openForm(latest);
      else closeForm();
      return;
    }
    if (action === "copy") {
      const latest = await findApplication(Number(form.elements.id.value)).catch(() => null);
      const draft = state.pendingConflictDraft;
      if (!latest || !draft) return;
      openForm({ ...latest, ...draft, id: latest.id, version: latest.version });
      showToast("Your changes are back in the form. Review them before saving.");
    }
  });
  $("#details-content").addEventListener("change", (event) => {
    if (event.target.id === "transition-stage") updateTransitionOutcomeVisibility();
  });
  $("#details-content").addEventListener("submit", (event) => {
    if (event.target.id === "activity-form") submitActivity(event);
    if (event.target.id === "task-form") submitTask(event);
    if (event.target.id === "artifact-form") submitArtifact(event);
    if (event.target.id === "submission-form") submitSubmission(event);
    if (event.target.id === "requirement-form") submitRequirement(event);
  });
  bindDialogBackdrop(detailsDialog, closeDetails);

  $$("[data-view]").forEach((button) => button.addEventListener("click", () => {
    state.view = button.dataset.view;
    state.page = 1;
    renderViewTabs();
    loadApplications().catch(() => {});
  }));
  $$('[data-display-view]').forEach((button) => button.addEventListener("click", () => {
    state.displayView = button.dataset.displayView;
    renderDisplayTabs();
    syncUrl();
    renderApplications();
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
  document.addEventListener("click", (event) => {
    const retry = event.target.closest("[data-region-retry]");
    if (!retry) return;
    const actions = {
      analytics: refreshAnalytics,
      insights: refreshInsights,
      today: refreshToday,
    };
    actions[retry.dataset.regionRetry]?.().catch(() => {});
  });
  $("#insights-window").addEventListener("change", (event) => {
    const previous = state.insightsWindow;
    const previousData = state.insights;
    state.insightsWindow = event.target.value;
    refreshInsights().catch(() => {
      state.insightsWindow = previous;
      state.insights = previousData;
      renderInsights();
    });
  });

  $("#export-trigger").addEventListener("click", (event) => { event.stopPropagation(); toggleExportMenu(); });
  $("#export-menu").addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-export]");
    if (trigger) { toggleExportMenu(false); exportWorkspace(trigger.dataset.export); }
  });
  document.addEventListener("click", (event) => { if (!event.target.closest(".menu-wrap")) toggleExportMenu(false); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("#export-menu").hidden) toggleExportMenu(false); });
  $("#export-menu").addEventListener("keydown", (event) => {
    const items = [...$("#export-menu").querySelectorAll("[role='menuitem']")];
    const currentIndex = items.indexOf(document.activeElement);
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === "ArrowUp" ? -1 : 1;
    const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (currentIndex + direction + items.length) % items.length;
    items[nextIndex]?.focus();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#search").focus();
      $("#search").select();
    }
  });

  $("#import-trigger").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", (event) => prepareImport(event.target.files[0]));
  $("#import-form").addEventListener("submit", importWorkspace);
  $("#close-import").addEventListener("click", () => importDialog.close());
  $("#cancel-import").addEventListener("click", () => importDialog.close());
  bindDialogBackdrop(importDialog, () => importDialog.close());
  $("#csv-form").addEventListener("submit", (event) => { event.preventDefault(); performCsvImport(); });
  $("#csv-duplicates").addEventListener("change", (event) => {
    if (event.target.matches("[data-merge-field-for]")) updateCsvImportButton();
  });
  $("#import-duplicates").addEventListener("change", (event) => {
    if (event.target.matches("[data-merge-field-for]")) updateJsonImportButton();
  });
  $("#download-csv-errors").addEventListener("click", () => {
    const pending = state.pendingCsvImport;
    if (!pending?.lastResult) return;
    const rows = [["row", "error"]];
    pending.lastResult.errors.forEach((message) => {
      const match = String(message).match(/^Row (\d+):\s*(.*)$/);
      rows.push([match?.[1] || "", match?.[2] || message]);
    });
    const previewInvalid = pending.duplicatePreview?.invalid || [];
    previewInvalid.forEach((item) => rows.push([Number(item.source_index ?? item.incoming_index ?? "") + 1, Object.values(item.errors || {}).join("; ")]));
    downloadFile(`${pending.fileName.replace(/\.[^.]+$/, "")}-errors.csv`, `\ufeff${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}\r\n`, "text/csv;charset=utf-8");
  });
  $("#close-csv").addEventListener("click", () => csvDialog.close());
  $("#cancel-csv").addEventListener("click", () => csvDialog.close());
  bindDialogBackdrop(csvDialog, () => csvDialog.close());

  $("#reset-demo").addEventListener("click", (event) => {
    if (!window.JobFlowDemoReset) return;
    askConfirmation({
      title: "Reset the demo workspace?",
      message: "Your browser changes will be replaced with the sample applications. This action cannot be undone.",
      confirmLabel: "Reset demo",
      trigger: event.currentTarget,
      onConfirm: async () => {
        window.JobFlowDemoReset();
        state.page = 1;
        await refreshWorkspace().catch(() => {});
        showToast("Demo workspace reset.");
      },
    });
  });
  $("#download-corrupt-cache").addEventListener("click", () => {
    const raw = window.JobFlowDemoRawCache?.();
    if (!raw) {
      showToast("The raw demo cache is no longer available.", true);
      return;
    }
    downloadFile("jobflow-demo-cache-recovery.json", raw, "application/json");
    showToast("Raw demo cache downloaded.");
  });
  $("#reset-corrupt-demo").addEventListener("click", () => {
    closeDemoRecovery();
    askConfirmation({
      title: "Reset the demo workspace?",
      message: "The unreadable browser cache will be replaced with the sample applications.",
      confirmLabel: "Reset demo",
      trigger: $("#reset-corrupt-demo"),
      onConfirm: resetCorruptDemo,
    });
  });
  $("#close-demo-recovery").addEventListener("click", closeDemoRecovery);
  $("#cancel-demo-recovery").addEventListener("click", closeDemoRecovery);
  bindDialogBackdrop(demoRecoveryDialog, closeDemoRecovery);
  $("#close-confirm").addEventListener("click", closeConfirmation);
  $("#cancel-confirm").addEventListener("click", closeConfirmation);
  $("#accept-confirm").addEventListener("click", acceptConfirmation);
  $("#toast-action").addEventListener("click", runToastAction);
  $("#toast-close").addEventListener("click", dismissToast);
  confirmDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeConfirmation();
  });
  bindDialogBackdrop(confirmDialog, closeConfirmation);
  window.addEventListener("popstate", () => {
    readUrlState();
    const detailId = Number(new URLSearchParams(window.location.search).get("detail"));
    if (detailId > 0) openDetails(detailId, { updateHistory: false });
    else if (detailsDialog.open) closeDetails();
    loadApplications().catch(() => {});
  });
}

async function initialize() {
  if (window.JobFlowDemoApi) {
    document.title = "JobFlow — Interactive Portfolio Demo";
    $("#demo-notice").hidden = false;
    $(".workspace-state strong").textContent = "Browser demo";
    $(".workspace-state small").textContent = "Local storage · No account needed";
    $("#runtime-label").textContent = "Browser demo";
    $("#runtime-detail").textContent = "Synthetic localStorage data · No account needed";
  } else {
    $("#runtime-label").textContent = "Local workspace";
    $("#runtime-detail").textContent = "Python API + SQLite · Data stays on this device";
  }
  setupSectionNavigation();
  bindEvents();
  openDemoRecoveryIfNeeded();
  try {
    state.options = await api("/api/meta/options");
    populateOptions();
    readUrlState();
    const initialDetail = Number(new URLSearchParams(window.location.search).get("detail"));
    await refreshWorkspace();
    if (initialDetail > 0) await openDetails(initialDetail, { updateHistory: false });
  } catch (error) {
    $(".table-wrap").setAttribute("aria-busy", "false");
    $("#loading-state").hidden = true;
    showSystemError("JobFlow could not start.", `${error.message} Check the local server and try again.`);
  }
}

initialize();
