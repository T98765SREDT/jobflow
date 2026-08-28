"use strict";

// GitHub Pages cannot run JobFlow's Python API. This adapter mirrors the API
// contract for a portfolio demo and keeps changes in versioned browser storage.
const previewParameters = new URLSearchParams(window.location.search);
const isPortfolioPreview = window.location.hostname.endsWith("github.io") || previewParameters.has("demo");

if (isPortfolioPreview) {
  const STORAGE_KEY = "jobflow.portfolio.v2";
  const SCHEMA_VERSION = 3;
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
  const eventTypes = ["applied", "status_changed", "interview", "follow_up", "note", "offer", "rejection", "custom"];

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
    const records = [
      { id: 1, company: "Northstar Labs", role: "Python Backend Developer", location: "Worldwide", work_mode: "Remote", status: "Interview", source: "LinkedIn", url: "https://example.com/jobs/northstar", salary_min: 32, salary_max: 48, salary_period: "Hourly", currency: "USD", applied_date: dateOffset(-8), next_action_date: dateOffset(-1), notes: "Prepare API design examples and questions for the engineering team.", created_at: created, updated_at: created },
      { id: 2, company: "Lumen AI", role: "AI Code Evaluator — Mandarin", location: "Japan", work_mode: "Remote", status: "Applied", source: "Company site", url: "https://example.com/jobs/lumen", salary_min: 28, salary_max: 40, salary_period: "Hourly", currency: "USD", applied_date: dateOffset(-3), next_action_date: dateOffset(4), notes: "Submitted coding assessment. Follow up if there is no response.", created_at: created, updated_at: created },
      { id: 3, company: "Sora Systems", role: "Junior Full-Stack Engineer", location: "Tokyo, Japan", work_mode: "Hybrid", status: "Wishlist", source: "Referral", url: "https://example.com/jobs/sora", salary_min: 4200000, salary_max: 5500000, salary_period: "Annual", currency: "JPY", applied_date: null, next_action_date: dateOffset(2), notes: "Tailor the portfolio summary to the product dashboard requirements.", created_at: created, updated_at: created },
      { id: 4, company: "Orbit QA", role: "Freelance Software Tester", location: "Worldwide", work_mode: "Remote", status: "Offer", source: "Remote board", url: "https://example.com/jobs/orbit", salary_min: 22, salary_max: 28, salary_period: "Hourly", currency: "USD", applied_date: dateOffset(-14), next_action_date: dateOffset(1), notes: "Review the contractor agreement and confirm weekly availability.", created_at: created, updated_at: created },
      { id: 5, company: "Maple Cloud", role: "Web Developer", location: "Singapore", work_mode: "Remote", status: "Rejected", source: "LinkedIn", url: "https://example.com/jobs/maple", salary_min: 3000, salary_max: 4500, salary_period: "Monthly", currency: "USD", applied_date: dateOffset(-25), next_action_date: null, notes: "Useful practice interview; strengthen system-design examples.", created_at: created, updated_at: created },
      { id: 6, company: "Kite Data", role: "Technical Data Analyst", location: "Japan", work_mode: "Remote", status: "Applied", source: "Company site", url: "https://example.com/jobs/kite", salary_min: 250000, salary_max: 350000, salary_period: "Monthly", currency: "JPY", applied_date: dateOffset(-1), next_action_date: dateOffset(6), notes: "Highlight SQL validation and structured-data experience.", created_at: created, updated_at: created },
    ];
    return records.map((application) => ({
      ...application,
      events: [{ id: application.id * 10, application_id: application.id, event_type: application.status === "Wishlist" ? "custom" : "applied", title: "Application added", details: "Sample activity for the portfolio demo.", occurred_at: created, created_at: created }],
    }));
  }

  function loadStored() {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (stored && [2, SCHEMA_VERSION].includes(stored.schema_version) && Array.isArray(stored.applications)) {
        return stored.applications.map((application) => ({ ...application, events: Array.isArray(application.events) ? application.events : [] }));
      }
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

  function validateEvent(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw makeError("Validation failed.", { body: "Expected an object." });
    const event = {
      event_type: String(payload.event_type || "").trim(),
      title: String(payload.title || "").trim(),
      details: String(payload.details || "").trim(),
      occurred_at: payload.occurred_at ? String(payload.occurred_at) : nowIso(),
    };
    const errors = {};
    if (!eventTypes.includes(event.event_type)) errors.event_type = `Choose one of: ${eventTypes.join(", ")}.`;
    if (!event.title) errors.title = "This field is required.";
    if (event.title.length > 160) errors.title = "Must be 160 characters or fewer.";
    if (event.details.length > 4000) errors.details = "Must be 4000 characters or fewer.";
    if (Number.isNaN(Date.parse(event.occurred_at))) errors.occurred_at = "Use an ISO date-time value.";
    if (Object.keys(errors).length) throw makeError("Validation failed.", errors);
    return event;
  }

  function addEvent(application, event) {
    const nextId = applications.flatMap((item) => item.events || []).reduce((maximum, item) => Math.max(maximum, item.id || 0), 0) + 1;
    application.events = [...(application.events || []), { id: nextId, application_id: application.id, ...event, created_at: nowIso() }];
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
    const today = todayIso();
    const dueSoon = dateOffset(7);
    const attention = applications
      .filter((application) => application.status !== "Rejected" && (
        (application.next_action_date && application.next_action_date <= dueSoon)
        || (!application.next_action_date && ["Applied", "Interview", "Offer"].includes(application.status))
      ))
      .map((application) => ({
        ...application,
        attention_type: application.next_action_date
          ? application.next_action_date < today ? "overdue" : application.next_action_date === today ? "today" : "due_soon"
          : "missing",
      }))
      .sort((left, right) => {
        const rank = { overdue: 0, today: 1, due_soon: 2, missing: 3 };
        const typeDifference = rank[left.attention_type] - rank[right.attention_type];
        if (typeDifference) return typeDifference;
        if (!left.next_action_date && !right.next_action_date) return right.id - left.id;
        if (!left.next_action_date) return 1;
        if (!right.next_action_date) return -1;
        return asDate(left.next_action_date) - asDate(right.next_action_date);
      });
    return {
      total: applications.length,
      active: applications.filter((application) => ["Applied", "Interview", "Offer"].includes(application.status)).length,
      interviews,
      submitted,
      response_rate: submitted ? Math.round((interviews / submitted) * 100) : 0,
      overdue: actionable.filter((application) => application.next_action_date < todayIso()).length,
      due_soon: actionable.filter((application) => application.next_action_date >= todayIso() && application.next_action_date <= dateOffset(7)).length,
      attention_total: attention.length,
      by_status: byStatus,
      by_work_mode: Object.fromEntries(previewOptions.work_modes.map((mode) => [mode, applications.filter((application) => application.work_mode === mode).length])),
      upcoming: actionable.sort((left, right) => asDate(left.next_action_date) - asDate(right.next_action_date)).slice(0, 5),
      attention: attention.slice(0, 8),
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
      const application = { id: nextId, ...cleaned, created_at: timestamp, updated_at: timestamp, events: [] };
      addEvent(application, {
        event_type: cleaned.status === "Wishlist" ? "custom" : "applied",
        title: "Application added",
        details: "Application entered the JobFlow workspace.",
        occurred_at: timestamp,
      });
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
      const imported = cleaned.map((record, index) => ({
        id: nextId++,
        ...record,
        created_at: timestamp,
        updated_at: timestamp,
        events: Array.isArray(payload.applications[index].events)
          ? payload.applications[index].events.map((event) => validateEvent(event)).map((event, eventIndex) => ({ id: eventIndex + 1, application_id: nextId - 1, ...event, created_at: timestamp }))
          : [],
      }));
      commit(mode === "replace" ? imported : [...applications, ...imported]);
      return { imported: imported.length, mode };
    }
    const eventsMatch = request.pathname.match(/^\/api\/applications\/(\d+)\/events$/);
    if (eventsMatch && method === "GET") {
      const application = applications.find((item) => item.id === Number(eventsMatch[1]));
      if (!application) throw makeError("Application not found.");
      return copy(application.events || []);
    }
    if (eventsMatch && method === "POST") {
      const application = applications.find((item) => item.id === Number(eventsMatch[1]));
      if (!application) throw makeError("Application not found.");
      const event = validateEvent(parseBody(options));
      const next = copy(applications);
      const target = next.find((item) => item.id === application.id);
      addEvent(target, event);
      commit(next);
      return copy(target.events[target.events.length - 1]);
    }
    const eventMatch = request.pathname.match(/^\/api\/applications\/(\d+)\/events\/(\d+)$/);
    if (eventMatch && method === "DELETE") {
      const application = applications.find((item) => item.id === Number(eventMatch[1]));
      if (!application) throw makeError("Application not found.");
      const eventId = Number(eventMatch[2]);
      const next = copy(applications);
      const target = next.find((item) => item.id === application.id);
      const events = target.events || [];
      if (!events.some((event) => event.id === eventId)) throw makeError("Event not found.");
      target.events = events.filter((event) => event.id !== eventId);
      commit(next);
      return null;
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
      if (cleaned.status !== applications[index].status) {
        addEvent(updated, { event_type: "status_changed", title: `Status changed to ${cleaned.status}`, details: `Previous status: ${applications[index].status}`, occurred_at: updated.updated_at });
      }
      if (cleaned.next_action_date !== applications[index].next_action_date) {
        addEvent(updated, { event_type: "follow_up", title: "Follow-up date updated", details: `Next action: ${cleaned.next_action_date || "No date"}`, occurred_at: updated.updated_at });
      }
      if (cleaned.notes !== applications[index].notes) {
        addEvent(updated, { event_type: "note", title: "Notes updated", details: cleaned.notes || "Notes cleared.", occurred_at: updated.updated_at });
      }
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
