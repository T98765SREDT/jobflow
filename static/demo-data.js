"use strict";

// GitHub Pages cannot run JobFlow's Python API. This adapter mirrors the API
// contract for a portfolio demo and keeps changes in versioned browser storage.
const previewParameters = new URLSearchParams(window.location.search);
const isPortfolioPreview = window.location.hostname.endsWith("github.io") || previewParameters.has("demo");

if (isPortfolioPreview) {
  const STORAGE_KEY = "jobflow.portfolio.v2";
  const SCHEMA_VERSION = 2;
  const previewOptions = {
    statuses: ["Wishlist", "Applied", "Interview", "Offer", "Rejected"],
    work_modes: ["Remote", "Hybrid", "On-site"],
    currencies: ["USD", "EUR", "JPY", "GBP", "CNY"],
    salary_periods: ["Hourly", "Monthly", "Annual"],
    views: ["active", "all", "follow-up", "interview", "offers"],
  };
  const allowedFields = [
    "company", "role", "location", "work_mode", "status", "source", "url",
    "salary_min", "salary_max", "salary_period", "currency", "applied_date", "next_action_date", "notes",
  ];

  const copy = (value) => JSON.parse(JSON.stringify(value));
  const pad = (number) => String(number).padStart(2, "0");
  const todayIso = () => {
    const now = new Date();
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  };
  const dateOffset = (days) => {
    const value = new Date();
    value.setHours(12, 0, 0, 0);
    value.setDate(value.getDate() + days);
    return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
  };
  const nowIso = () => new Date().toISOString();
  const asDate = (value) => value ? new Date(`${value}T00:00:00`).valueOf() : null;

  function sampleApplications() {
    const created = nowIso();
    return [
      { id: 1, company: "Northstar Labs", role: "Python Backend Developer", location: "Worldwide", work_mode: "Remote", status: "Interview", source: "LinkedIn", url: "https://example.com/jobs/northstar", salary_min: 32, salary_max: 48, salary_period: "Hourly", currency: "USD", applied_date: dateOffset(-8), next_action_date: dateOffset(-1), notes: "Prepare API design examples and questions for the engineering team.", created_at: created, updated_at: created },
      { id: 2, company: "Lumen AI", role: "AI Code Evaluator — Mandarin", location: "Japan", work_mode: "Remote", status: "Applied", source: "Company site", url: "https://example.com/jobs/lumen", salary_min: 28, salary_max: 40, salary_period: "Hourly", currency: "USD", applied_date: dateOffset(-3), next_action_date: dateOffset(4), notes: "Submitted coding assessment. Follow up if there is no response.", created_at: created, updated_at: created },
      { id: 3, company: "Sora Systems", role: "Junior Full-Stack Engineer", location: "Tokyo, Japan", work_mode: "Hybrid", status: "Wishlist", source: "Referral", url: "https://example.com/jobs/sora", salary_min: 4200000, salary_max: 5500000, salary_period: "Annual", currency: "JPY", applied_date: null, next_action_date: dateOffset(2), notes: "Tailor the portfolio summary to the product dashboard requirements.", created_at: created, updated_at: created },
      { id: 4, company: "Orbit QA", role: "Freelance Software Tester", location: "Worldwide", work_mode: "Remote", status: "Offer", source: "Remote board", url: "https://example.com/jobs/orbit", salary_min: 22, salary_max: 28, salary_period: "Hourly", currency: "USD", applied_date: dateOffset(-14), next_action_date: dateOffset(1), notes: "Review the contractor agreement and confirm weekly availability.", created_at: created, updated_at: created },
      { id: 5, company: "Maple Cloud", role: "Web Developer", location: "Singapore", work_mode: "Remote", status: "Rejected", source: "LinkedIn", url: "https://example.com/jobs/maple", salary_min: 3000, salary_max: 4500, salary_period: "Monthly", currency: "USD", applied_date: dateOffset(-25), next_action_date: null, notes: "Useful practice interview; strengthen system-design examples.", created_at: created, updated_at: created },
      { id: 6, company: "Kite Data", role: "Technical Data Analyst", location: "Japan", work_mode: "Remote", status: "Applied", source: "Company site", url: "https://example.com/jobs/kite", salary_min: 250000, salary_max: 350000, salary_period: "Monthly", currency: "JPY", applied_date: dateOffset(-1), next_action_date: dateOffset(6), notes: "Highlight SQL validation and structured-data experience.", created_at: created, updated_at: created },
    ];
  }

  function loadStored() {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (stored?.schema_version === SCHEMA_VERSION && Array.isArray(stored.applications)) return stored.applications;
    } catch (_error) {
      // A malformed demo cache should never make the public preview unusable.
    }
    return sampleApplications();
  }

  let applications = loadStored();

  function commit(nextApplications) {
    const payload = { schema_version: SCHEMA_VERSION, saved_at: nowIso(), applications: nextApplications };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    applications = nextApplications;
  }

  function makeError(message, fields = {}) {
    const error = new Error(message);
    error.fields = fields;
    return error;
  }

  function validateDate(value, field, errors) {
    if (value == null || value === "") return null;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value)) || Number.isNaN(asDate(value))) errors[field] = "Use ISO format YYYY-MM-DD.";
    return String(value);
  }

  function validateSalary(value, field, errors) {
    if (value == null || value === "") return null;
    const amount = Number(value);
    if (!Number.isFinite(amount) || amount < 0 || !/^\d+(?:\.\d{1,2})?$/.test(String(value))) {
      errors[field] = "Enter a non-negative amount with no more than two decimal places.";
      return null;
    }
    return amount;
  }

  function validateApplication(payload, existing = null) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw makeError("Validation failed.", { body: "Expected an object." });
    const errors = {};
    const source = { ...(existing || {}), ...payload };
    const cleaned = {};
    for (const field of allowedFields) cleaned[field] = source[field] ?? null;
    for (const field of ["company", "role", "status", "work_mode"]) {
      cleaned[field] = String(cleaned[field] ?? "").trim();
      if (!cleaned[field]) errors[field] = "This field is required.";
    }
    for (const [field, maximum] of [["company", 120], ["role", 160], ["location", 120], ["source", 80], ["url", 500], ["notes", 4000]]) {
      cleaned[field] = String(cleaned[field] ?? "").trim();
      if (cleaned[field].length > maximum) errors[field] = `Must be ${maximum} characters or fewer.`;
    }
    if (!previewOptions.statuses.includes(cleaned.status)) errors.status = `Choose one of: ${previewOptions.statuses.join(", ")}.`;
    if (!previewOptions.work_modes.includes(cleaned.work_mode)) errors.work_mode = `Choose one of: ${previewOptions.work_modes.join(", ")}.`;
    cleaned.currency = String(cleaned.currency || "USD").toUpperCase();
    cleaned.salary_period = String(cleaned.salary_period || "Annual");
    if (!previewOptions.currencies.includes(cleaned.currency)) errors.currency = `Choose one of: ${previewOptions.currencies.join(", ")}.`;
    if (!previewOptions.salary_periods.includes(cleaned.salary_period)) errors.salary_period = `Choose one of: ${previewOptions.salary_periods.join(", ")}.`;
    cleaned.salary_min = validateSalary(cleaned.salary_min, "salary_min", errors);
    cleaned.salary_max = validateSalary(cleaned.salary_max, "salary_max", errors);
    cleaned.applied_date = validateDate(cleaned.applied_date, "applied_date", errors);
    cleaned.next_action_date = validateDate(cleaned.next_action_date, "next_action_date", errors);
    if (cleaned.salary_min != null && cleaned.salary_max != null && cleaned.salary_max < cleaned.salary_min) errors.salary_max = "Maximum salary cannot be below minimum salary.";
    if (cleaned.applied_date && cleaned.next_action_date && cleaned.next_action_date < cleaned.applied_date) errors.next_action_date = "Next action cannot be before the applied date.";
    if (cleaned.url) {
      try {
        const parsed = new URL(cleaned.url);
        if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) throw new Error();
      } catch (_error) {
        errors.url = "Enter a complete http:// or https:// URL.";
      }
    }
    if (Object.keys(errors).length) throw makeError("Validation failed.", errors);
    return cleaned;
  }

  function list(query) {
    const search = (query.get("search") || "").toLowerCase();
    const status = query.get("status") || "";
    const workMode = query.get("work_mode") || "";
    const view = query.get("view") || "all";
    const sort = query.get("sort") || "updated_at";
    const direction = (query.get("direction") || "desc").toLowerCase() === "asc" ? 1 : -1;
    const page = Math.max(1, Number.parseInt(query.get("page") || "1", 10) || 1);
    const limit = [10, 20, 50].includes(Number(query.get("limit"))) ? Number(query.get("limit")) : 20;
    const dueCutoff = dateOffset(7);
    const filtered = applications.filter((application) => {
      const haystack = [application.company, application.role, application.location, application.notes].join(" ").toLowerCase();
      const viewMatch = view === "all"
        || (view === "active" && ["Applied", "Interview", "Offer"].includes(application.status))
        || (view === "follow-up" && application.next_action_date && application.next_action_date <= dueCutoff && application.status !== "Rejected")
        || (view === "interview" && application.status === "Interview")
        || (view === "offers" && application.status === "Offer");
      return viewMatch && (!search || haystack.includes(search)) && (!status || application.status === status) && (!workMode || application.work_mode === workMode);
    }).sort((left, right) => {
      if (["applied_date", "next_action_date"].includes(sort) && (!left[sort] || !right[sort])) {
        if (!left[sort] && !right[sort]) return right.id - left.id;
        return left[sort] ? -1 : 1;
      }
      const leftValue = ["applied_date", "next_action_date"].includes(sort) ? asDate(left[sort]) : String(left[sort] || "").toLowerCase();
      const rightValue = ["applied_date", "next_action_date"].includes(sort) ? asDate(right[sort]) : String(right[sort] || "").toLowerCase();
      return (leftValue > rightValue ? 1 : leftValue < rightValue ? -1 : right.id - left.id) * direction;
    });
    const offset = (page - 1) * limit;
    return { items: filtered.slice(offset, offset + limit), count: Math.min(limit, Math.max(0, filtered.length - offset)), total: filtered.length, page, page_size: limit };
  }

  function analytics() {
    const byStatus = Object.fromEntries(previewOptions.statuses.map((status) => [status, 0]));
    applications.forEach((application) => { byStatus[application.status] += 1; });
    const submitted = applications.filter((application) => application.status !== "Wishlist").length;
    const interviews = applications.filter((application) => ["Interview", "Offer"].includes(application.status)).length;
    const actionable = applications.filter((application) => application.next_action_date && application.status !== "Rejected");
    return {
      total: applications.length,
      active: applications.filter((application) => ["Applied", "Interview", "Offer"].includes(application.status)).length,
      interviews,
      submitted,
      response_rate: submitted ? Math.round((interviews / submitted) * 100) : 0,
      overdue: actionable.filter((application) => application.next_action_date < todayIso()).length,
      due_soon: actionable.filter((application) => application.next_action_date >= todayIso() && application.next_action_date <= dateOffset(7)).length,
      by_status: byStatus,
      by_work_mode: Object.fromEntries(previewOptions.work_modes.map((mode) => [mode, applications.filter((application) => application.work_mode === mode).length])),
      upcoming: actionable.sort((left, right) => asDate(left.next_action_date) - asDate(right.next_action_date)).slice(0, 5),
    };
  }

  const parseBody = (options) => {
    try { return options.body ? JSON.parse(options.body) : {}; }
    catch (_error) { throw makeError("Request body must contain valid JSON."); }
  };

  window.JobFlowDemoReset = () => commit(sampleApplications());

  window.JobFlowDemoApi = async (path, options = {}) => {
    const request = new URL(path, "https://jobflow.preview");
    const method = (options.method || "GET").toUpperCase();
    if (request.pathname === "/api/meta/options" && method === "GET") return copy(previewOptions);
    if (request.pathname === "/api/analytics" && method === "GET") return copy(analytics());
    if (request.pathname === "/api/export" && method === "GET") return copy({ schema_version: SCHEMA_VERSION, exported_at: nowIso(), applications });
    if (request.pathname === "/api/applications" && method === "GET") return copy(list(request.searchParams));
    if (request.pathname === "/api/applications" && method === "POST") {
      const cleaned = validateApplication(parseBody(options));
      const nextId = applications.reduce((maximum, application) => Math.max(maximum, application.id), 0) + 1;
      const timestamp = nowIso();
      const application = { id: nextId, ...cleaned, created_at: timestamp, updated_at: timestamp };
      commit([application, ...applications]);
      return copy(application);
    }
    if (request.pathname === "/api/import" && method === "POST") {
      const payload = parseBody(options);
      if (!payload || !Array.isArray(payload.applications)) throw makeError("Import validation failed.", { body: "Expected an object with an applications array." });
      if (payload.schema_version && payload.schema_version > SCHEMA_VERSION) throw makeError("Import validation failed.", { schema_version: "Backup schema is newer than this demo." });
      if (payload.applications.length > 5000) throw makeError("Import validation failed.", { applications: "A backup can contain at most 5,000 records." });
      const cleaned = payload.applications.map((record) => validateApplication(record));
      const mode = request.searchParams.get("mode") || "append";
      if (!["append", "replace"].includes(mode)) throw makeError("Import validation failed.", { mode: "Choose append or replace." });
      let nextId = mode === "replace" ? 1 : applications.reduce((maximum, application) => Math.max(maximum, application.id), 0) + 1;
      const timestamp = nowIso();
      const imported = cleaned.map((record) => ({ id: nextId++, ...record, created_at: timestamp, updated_at: timestamp }));
      commit(mode === "replace" ? imported : [...applications, ...imported]);
      return { imported: imported.length, mode };
    }
    const match = request.pathname.match(/^\/api\/applications\/(\d+)$/);
    if (match && method === "GET") {
      const application = applications.find((item) => item.id === Number(match[1]));
      if (!application) throw makeError("Application not found.");
      return copy(application);
    }
    if (match && method === "PATCH") {
      const id = Number(match[1]);
      const index = applications.findIndex((application) => application.id === id);
      if (index < 0) throw makeError("Application not found.");
      const cleaned = validateApplication(parseBody(options), applications[index]);
      const updated = { ...applications[index], ...cleaned, updated_at: nowIso() };
      const next = [...applications];
      next[index] = updated;
      commit(next);
      return copy(updated);
    }
    if (match && method === "DELETE") {
      const id = Number(match[1]);
      if (!applications.some((application) => application.id === id)) throw makeError("Application not found.");
      commit(applications.filter((application) => application.id !== id));
      return null;
    }
    throw makeError("This preview route is unavailable.");
  };
}
