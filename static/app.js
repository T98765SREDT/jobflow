"use strict";

const state = { applications: [], analytics: null, options: null, searchTimer: null };
const $ = (selector) => document.querySelector(selector);
const form = $("#application-form");
const dialog = $("#application-dialog");

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({ error: "The server returned an invalid response." }));
  if (!response.ok) {
    const error = new Error(data.error || "Request failed.");
    error.fields = data.fields || {};
    throw error;
  }
  return data;
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function formatDate(value) {
  if (!value) return "—";
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric" }).format(parsed);
}

function isDueSoon(value) {
  if (!value) return false;
  const difference = new Date(`${value}T23:59:59`) - new Date();
  return difference < 3 * 86400000;
}

function initials(company) {
  return company.split(/\s+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase();
}

function populateOptions() {
  const groups = [
    [$("#status-filter"), state.options.statuses, ""],
    [$("#mode-filter"), state.options.work_modes, ""],
    [form.elements.status, state.options.statuses, "Wishlist"],
    [form.elements.work_mode, state.options.work_modes, "Remote"],
    [form.elements.currency, state.options.currencies, "USD"],
  ];
  for (const [select, values, defaultValue] of groups) {
    const first = select.querySelector("option")?.outerHTML || "";
    select.innerHTML = first + values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
    if (defaultValue) select.value = defaultValue;
  }
}

function renderApplications() {
  const body = $("#applications-body");
  body.innerHTML = state.applications.map((application) => `
    <tr>
      <td><div class="opportunity"><span class="company-avatar">${escapeHtml(initials(application.company))}</span><div><strong>${escapeHtml(application.role)}</strong><small>${escapeHtml(application.company)} · ${escapeHtml(application.location || "Location flexible")}</small></div></div></td>
      <td><span class="status status-${application.status.toLowerCase()}">${escapeHtml(application.status)}</span></td>
      <td><span class="mode">${escapeHtml(application.work_mode)}</span></td>
      <td><span class="date">${formatDate(application.applied_date)}</span></td>
      <td><span class="date ${isDueSoon(application.next_action_date) ? "due" : ""}">${formatDate(application.next_action_date)}</span></td>
      <td><div class="row-actions"><button class="icon-button" data-edit="${application.id}" aria-label="Edit ${escapeHtml(application.company)}">✎</button><button class="icon-button danger" data-delete="${application.id}" aria-label="Delete ${escapeHtml(application.company)}">×</button></div></td>
    </tr>`).join("");
  $("#loading-state").style.display = "none";
  $("#empty-state").hidden = state.applications.length !== 0;
  $("#result-count").textContent = `${state.applications.length} ${state.applications.length === 1 ? "opportunity" : "opportunities"}`;
}

function renderAnalytics() {
  const data = state.analytics;
  $("#stat-total").textContent = data.total;
  $("#stat-active").textContent = data.active;
  $("#stat-interviews").textContent = data.interviews;
  $("#stat-rate").textContent = `${data.response_rate}%`;

  const max = Math.max(...Object.values(data.by_status), 1);
  $("#stage-chart").innerHTML = state.options.statuses.map((status) => {
    const count = data.by_status[status] || 0;
    return `<div class="stage-row"><span>${status}</span><div class="bar-track"><div class="bar-fill" style="width:${(count / max) * 100}%"></div></div><strong>${count}</strong></div>`;
  }).join("");

  $("#upcoming-list").innerHTML = data.upcoming.length ? data.upcoming.map((item) => {
    const date = item.next_action_date ? new Date(`${item.next_action_date}T00:00:00`) : null;
    return `<div class="upcoming-item"><div class="date-box">${date ? date.toLocaleString("en", { month: "short" }) : "—"}<strong>${date ? date.getDate() : ""}</strong></div><div><h3>${escapeHtml(item.role)}</h3><p>${escapeHtml(item.company)} · ${escapeHtml(item.status)}</p></div><span class="arrow">›</span></div>`;
  }).join("") : `<div class="empty-state"><p>No upcoming actions yet.</p></div>`;
}

async function loadApplications() {
  const params = new URLSearchParams();
  const values = {
    search: $("#search").value.trim(), status: $("#status-filter").value,
    work_mode: $("#mode-filter").value, sort: $("#sort").value,
    direction: ["company", "next_action_date"].includes($("#sort").value) ? "asc" : "desc",
  };
  Object.entries(values).forEach(([key, value]) => value && params.set(key, value));
  try {
    const result = await api(`/api/applications?${params}`);
    state.applications = result.items;
    renderApplications();
  } catch (error) {
    showToast(error.message, true);
    $("#loading-state").style.display = "none";
  }
}

async function refreshAnalytics() {
  state.analytics = await api("/api/analytics");
  renderAnalytics();
}

function openForm(application = null) {
  form.reset();
  form.elements.status.value = "Wishlist";
  form.elements.work_mode.value = "Remote";
  form.elements.currency.value = "USD";
  $("#form-errors").hidden = true;
  $("#dialog-title").textContent = application ? "Edit application" : "Add application";
  if (application) {
    Object.keys(application).forEach((key) => {
      if (form.elements[key] && application[key] !== null) form.elements[key].value = application[key];
    });
  }
  dialog.showModal();
  setTimeout(() => form.elements.company.focus(), 50);
}

function closeForm() { dialog.close(); }

function serializeForm() {
  const values = Object.fromEntries(new FormData(form));
  delete values.id;
  for (const field of ["salary_min", "salary_max"]) values[field] = values[field] === "" ? null : Number(values[field]);
  for (const field of ["applied_date", "next_action_date"]) values[field] = values[field] || null;
  return values;
}

function showFormErrors(error) {
  const messages = Object.entries(error.fields || {}).map(([field, message]) => `<li><strong>${escapeHtml(field.replaceAll("_", " "))}:</strong> ${escapeHtml(message)}</li>`);
  const box = $("#form-errors");
  box.innerHTML = messages.length ? `<ul>${messages.join("")}</ul>` : escapeHtml(error.message);
  box.hidden = false;
}

let toastTimer;
function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast visible${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.className = "toast", 2800);
}

async function submitForm(event) {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const id = form.elements.id.value;
  const button = $("#save-application");
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    await api(id ? `/api/applications/${id}` : "/api/applications", { method: id ? "PATCH" : "POST", body: JSON.stringify(serializeForm()) });
    closeForm();
    showToast(id ? "Application updated." : "Application added to your pipeline.");
    await Promise.all([loadApplications(), refreshAnalytics()]);
  } catch (error) {
    showFormErrors(error);
  } finally {
    button.disabled = false;
    button.textContent = "Save application";
  }
}

async function deleteApplication(id) {
  const application = state.applications.find((item) => item.id === id);
  if (!application || !confirm(`Delete the ${application.role} application at ${application.company}?`)) return;
  try {
    await api(`/api/applications/${id}`, { method: "DELETE" });
    showToast("Application deleted.");
    await Promise.all([loadApplications(), refreshAnalytics()]);
  } catch (error) { showToast(error.message, true); }
}

function bindEvents() {
  $("#add-application").addEventListener("click", () => openForm());
  $("#close-dialog").addEventListener("click", closeForm);
  $("#cancel-dialog").addEventListener("click", closeForm);
  form.addEventListener("submit", submitForm);
  dialog.addEventListener("click", (event) => { if (event.target === dialog) closeForm(); });
  $("#applications-body").addEventListener("click", (event) => {
    const editButton = event.target.closest("[data-edit]");
    const deleteButton = event.target.closest("[data-delete]");
    if (editButton) openForm(state.applications.find((item) => item.id === Number(editButton.dataset.edit)));
    if (deleteButton) deleteApplication(Number(deleteButton.dataset.delete));
  });
  [$("#status-filter"), $("#mode-filter"), $("#sort")].forEach((element) => element.addEventListener("change", loadApplications));
  $("#search").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(loadApplications, 250);
  });
}

async function initialize() {
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  $("#greeting").textContent = `${greeting}, Haoran.`;
  bindEvents();
  try {
    state.options = await api("/api/meta/options");
    populateOptions();
    await Promise.all([loadApplications(), refreshAnalytics()]);
  } catch (error) {
    showToast(`Could not start JobFlow: ${error.message}`, true);
    $("#loading-state").textContent = "Unable to load the application data.";
  }
}

initialize();
