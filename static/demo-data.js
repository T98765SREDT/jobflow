"use strict";

// GitHub Pages cannot run JobFlow's Python API. This adapter mirrors the API
// contract for a portfolio demo and keeps changes in versioned browser storage.
const previewParameters = new URLSearchParams(window.location.search);
const isPortfolioPreview = window.location.hostname.endsWith("github.io") || previewParameters.has("demo");

if (isPortfolioPreview) {
  const STORAGE_KEY = "jobflow.portfolio.v2";
  const SCHEMA_VERSION = 8;
  const statuses = ["Wishlist", "Applied", "Interview", "Offer", "Rejected"];
  const stages = ["Wishlist", "Ready", "Applied", "Interview", "Offer", "Closed"];
  const outcomes = ["Rejected", "Withdrawn", "No response", "Expired", "Offer declined", "Accepted"];
  const legacyToStage = { Wishlist: "Wishlist", Applied: "Applied", Interview: "Interview", Offer: "Offer", Rejected: "Closed" };
  const stageToLegacy = { Wishlist: "Wishlist", Ready: "Applied", Applied: "Applied", Interview: "Interview", Offer: "Offer", Closed: "Rejected" };
  const taskKinds = ["follow_up", "preparation", "interview", "decision", "custom"];
  const requirementCategories = ["skill", "experience", "language", "location", "work_authorization", "compensation", "other"];
  const requirementAssessments = ["met", "partial", "gap", "unknown"];
  const artifactKinds = ["job_description", "resume", "cover_letter", "portfolio", "assessment", "other"];
  const previewOptions = {
    schema_version: SCHEMA_VERSION,
    statuses,
    stages,
    outcomes,
    work_modes: ["Remote", "Hybrid", "On-site"],
    currencies: ["USD", "EUR", "JPY", "GBP", "CNY"],
    salary_periods: ["Hourly", "Monthly", "Annual"],
    task_kinds: taskKinds,
    requirement_categories: requirementCategories,
    requirement_assessments: requirementAssessments,
    artifact_kinds: artifactKinds,
    views: ["active", "all", "follow-up", "interview", "offers"],
  };
  const allowedFields = [
    "company", "role", "location", "work_mode", "status", "stage", "outcome", "version", "closed_at", "waiting_until", "source", "url",
    "salary_min", "salary_max", "salary_period", "currency", "applied_date", "next_action_date", "notes",
  ];
  const IMPORT_MERGE_FIELDS = ["company", "role", "location", "work_mode", "source", "url", "salary_min", "salary_max", "salary_period", "currency", "applied_date", "notes"];
  const eventTypes = ["applied", "status_changed", "interview", "follow_up", "note", "offer", "rejection", "custom"];
  const eventOrigins = ["system", "user", "import", "legacy"];
  const allowedTransitions = {
    Wishlist: new Set(["Ready", "Applied", "Closed"]),
    Ready: new Set(["Wishlist", "Applied", "Closed"]),
    Applied: new Set(["Wishlist", "Ready", "Interview", "Closed"]),
    Interview: new Set(["Applied", "Offer", "Closed"]),
    Offer: new Set(["Interview", "Closed"]),
    Closed: new Set(),
  };

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

  function migrateApplication(record) {
    const application = { ...record };
    const stage = stages.includes(application.stage)
      ? application.stage
      : legacyToStage[application.status] || "Wishlist";
    application.stage = stage;
    application.status = stageToLegacy[stage];
    application.outcome = stage === "Closed"
      ? (outcomes.includes(application.outcome) ? application.outcome : "Rejected")
      : null;
    application.closed_at = stage === "Closed"
      ? (application.closed_at || application.updated_at || nowIso())
      : null;
    if (stage === "Closed") application.waiting_until = null;
    application.version = Number.isInteger(application.version) && application.version > 0 ? application.version : 1;
    application.waiting_until = validateDateValue(application.waiting_until);
    const sourceTasks = Array.isArray(application.tasks) ? application.tasks : [];
    application.tasks = sourceTasks.map((task, index) => migrateTask(task, application.id, index + 1));
    const sourceRequirements = Array.isArray(application.requirements) ? application.requirements : [];
    application.requirements = sourceRequirements.map((requirement, index) => migrateRequirement(requirement, application.id, index + 1));
    const sourceArtifacts = Array.isArray(application.artifacts) ? application.artifacts : [];
    application.artifacts = sourceArtifacts.map((artifact, index) => migrateArtifact(artifact, application.id, index + 1));
    const sourceSubmissions = Array.isArray(application.submissions) ? application.submissions : [];
    application.submissions = sourceSubmissions.map((submission, index) => migrateSubmission(submission, application.id, application.artifacts, index + 1));
    if (!application.tasks.some((task) => !task.completed_at && task.due_date === application.next_action_date) && application.next_action_date && application.stage !== "Closed") {
      application.tasks.push({ id: Number(application.id) * 1000 + application.tasks.length + 1, application_id: application.id, kind: "follow_up", title: "Follow up", due_date: application.next_action_date, completed_at: null, version: 1, created_at: nowIso(), updated_at: nowIso() });
    }
    syncNextActionDate(application);
    return application;
  }

  function validateDateValue(value) {
    if (value == null || value === "") return null;
    return /^\d{4}-\d{2}-\d{2}$/.test(String(value)) && !Number.isNaN(asDate(value)) ? String(value) : null;
  }

  function migrateTask(task, applicationId, fallbackId) {
    const migrated = { ...task, application_id: applicationId };
    migrated.id = Number.isInteger(migrated.id) && migrated.id > 0 ? migrated.id : Number(applicationId) * 1000 + fallbackId;
    migrated.kind = taskKinds.includes(migrated.kind) ? migrated.kind : "custom";
    migrated.title = String(migrated.title || "Task").trim().slice(0, 200) || "Task";
    migrated.due_date = validateDateValue(migrated.due_date) || todayIso();
    migrated.completed_at = migrated.completed_at ? String(migrated.completed_at) : null;
    migrated.version = Number.isInteger(migrated.version) && migrated.version > 0 ? migrated.version : 1;
    migrated.created_at = migrated.created_at || nowIso();
    migrated.updated_at = migrated.updated_at || migrated.created_at;
    return migrated;
  }

  function migrateRequirement(requirement, applicationId, fallbackId) {
    const migrated = { ...requirement, application_id: applicationId };
    migrated.id = Number.isInteger(migrated.id) && migrated.id > 0 ? migrated.id : Number(applicationId) * 10000 + fallbackId;
    migrated.criterion = String(migrated.criterion || "Requirement").trim().slice(0, 240) || "Requirement";
    migrated.category = requirementCategories.includes(migrated.category) ? migrated.category : "other";
    migrated.assessment = requirementAssessments.includes(migrated.assessment) ? migrated.assessment : "unknown";
    migrated.evidence = String(migrated.evidence || "").trim().slice(0, 2000);
    migrated.weight = Number.isInteger(migrated.weight) && migrated.weight >= 1 && migrated.weight <= 5 ? migrated.weight : 1;
    migrated.position = Number.isInteger(migrated.position) && migrated.position >= 0 ? migrated.position : fallbackId - 1;
    migrated.created_at = migrated.created_at || nowIso();
    migrated.updated_at = migrated.updated_at || migrated.created_at;
    return migrated;
  }

  function migrateArtifact(artifact, applicationId, fallbackId) {
    const migrated = { ...artifact, application_id: applicationId };
    migrated.id = Number.isInteger(migrated.id) && migrated.id > 0 ? migrated.id : Number(applicationId) * 10000 + 500 + fallbackId;
    migrated.kind = artifactKinds.includes(migrated.kind) ? migrated.kind : "other";
    migrated.label = String(migrated.label || "Material").trim().slice(0, 160) || "Material";
    migrated.uri = String(migrated.uri || "").trim().slice(0, 500);
    migrated.version_label = String(migrated.version_label || "").trim().slice(0, 80);
    migrated.notes = String(migrated.notes || "").trim().slice(0, 2000);
    migrated.created_at = migrated.created_at || nowIso();
    migrated.updated_at = migrated.updated_at || migrated.created_at;
    return migrated;
  }

  function migrateSubmission(submission, applicationId, artifacts, fallbackId) {
    const migrated = { ...submission, application_id: applicationId };
    migrated.id = Number.isInteger(migrated.id) && migrated.id > 0 ? migrated.id : Number(applicationId) * 10000 + 800 + fallbackId;
    migrated.submitted_at = submission.submitted_at || nowIso();
    migrated.notes = String(submission.notes || "").trim().slice(0, 2000);
    const byId = new Map(artifacts.map((artifact) => [artifact.id, artifact]));
    migrated.items = (Array.isArray(submission.items) ? submission.items : []).map((item, index) => {
      const artifact = byId.get(item.artifact_id);
      if (!artifact) return null;
      return {
        package_id: migrated.id,
        artifact_id: artifact.id,
        position: index,
        snapshot_kind: artifactKinds.includes(item.snapshot_kind) ? item.snapshot_kind : artifact.kind,
        snapshot_label: String(item.snapshot_label || artifact.label).trim().slice(0, 160),
        snapshot_uri: String(item.snapshot_uri || artifact.uri).trim().slice(0, 500),
        snapshot_version_label: String(item.snapshot_version_label || artifact.version_label).trim().slice(0, 80),
        snapshot_notes: String(item.snapshot_notes || artifact.notes).trim().slice(0, 2000),
      };
    }).filter(Boolean);
    migrated.created_at = migrated.created_at || migrated.submitted_at;
    return migrated;
  }

  function syncNextActionDate(application) {
    const open = (application.tasks || []).filter((task) => !task.completed_at).sort((left, right) => asDate(left.due_date) - asDate(right.due_date) || left.id - right.id);
    application.next_action_date = open.length ? open[0].due_date : null;
  }

  function summarizeRequirements(requirements) {
    const counts = Object.fromEntries(requirementAssessments.map((assessment) => [assessment, 0]));
    let knownWeight = 0;
    let coveredWeight = 0;
    let missingEvidenceMet = 0;
    requirements.forEach((requirement) => {
      const assessment = requirementAssessments.includes(requirement.assessment) ? requirement.assessment : "unknown";
      counts[assessment] += 1;
      const weight = Number.isInteger(requirement.weight) ? requirement.weight : 1;
      if (assessment === "unknown") return;
      knownWeight += weight;
      if (assessment === "met") {
        coveredWeight += weight;
        if (!String(requirement.evidence || "").trim()) missingEvidenceMet += 1;
      } else if (assessment === "partial") {
        coveredWeight += weight * 0.5;
      }
    });
    const coverage = knownWeight ? Math.round((coveredWeight / knownWeight) * 1000) / 10 : null;
    return {
      total: requirements.length,
      counts,
      known_count: requirements.length - counts.unknown,
      known_weight: knownWeight,
      covered_weight: coveredWeight,
      coverage,
      known_weight_coverage: coverage,
      missing_evidence_met: missingEvidenceMet,
    };
  }

  function reconcileCompatibilityTask(application, nextDate) {
    const openFollowUp = (application.tasks || []).find((task) => !task.completed_at && task.kind === "follow_up");
    if (nextDate) {
      if (openFollowUp) {
        openFollowUp.due_date = nextDate;
        openFollowUp.version = (openFollowUp.version || 1) + 1;
        openFollowUp.updated_at = nowIso();
      } else {
        const nextId = applications.flatMap((item) => item.tasks || []).reduce((maximum, item) => Math.max(maximum, item.id || 0), 0) + 1;
        application.tasks = application.tasks || [];
        application.tasks.push(migrateTask({ id: nextId, kind: "follow_up", title: "Follow up", due_date: nextDate }, application.id, nextId));
      }
    } else {
      application.tasks = (application.tasks || []).map((task) => task.completed_at ? task : { ...task, completed_at: nowIso(), version: (task.version || 1) + 1, updated_at: nowIso() });
    }
    syncNextActionDate(application);
  }

  function migrateEvent(event, applicationId) {
    const migrated = { ...event, application_id: applicationId };
    migrated.origin = eventOrigins.includes(migrated.origin) ? migrated.origin : "legacy";
    migrated.from_stage = stages.includes(migrated.from_stage) ? migrated.from_stage : null;
    migrated.to_stage = stages.includes(migrated.to_stage) ? migrated.to_stage : null;
    migrated.payload_json = typeof migrated.payload_json === "string"
      ? migrated.payload_json
      : JSON.stringify(migrated.payload_json && typeof migrated.payload_json === "object" ? migrated.payload_json : {});
    migrated.request_id = migrated.request_id ? String(migrated.request_id) : null;
    return migrated;
  }

  function sampleRequirements(applicationId) {
    const examples = {
      1: [
        { criterion: "Python API development", category: "skill", assessment: "met", evidence: "Built and tested small REST-style services.", weight: 5, position: 0 },
        { criterion: "Experience with SQL", category: "skill", assessment: "partial", evidence: "Comfortable with SQLite and common joins; reviewing query tuning.", weight: 3, position: 1 },
        { criterion: "Availability for remote work", category: "location", assessment: "met", evidence: "Based in Japan and available for remote contractor work.", weight: 2, position: 2 },
      ],
      2: [
        { criterion: "Fluent Mandarin", category: "language", assessment: "met", evidence: "Native Mandarin speaker.", weight: 5, position: 0 },
        { criterion: "Python code review", category: "skill", assessment: "partial", evidence: "Coursework and portfolio projects; assessment pending.", weight: 4, position: 1 },
        { criterion: "Contractor work authorization", category: "work_authorization", assessment: "unknown", evidence: "Confirm project-specific requirements before accepting.", weight: 5, position: 2 },
      ],
      3: [
        { criterion: "Frontend framework experience", category: "skill", assessment: "gap", evidence: "No production React project yet.", weight: 3, position: 0 },
        { criterion: "Hybrid Tokyo availability", category: "location", assessment: "partial", evidence: "Need to confirm weekly office expectation.", weight: 4, position: 1 },
      ],
      4: [
        { criterion: "Manual QA and bug reporting", category: "skill", assessment: "met", evidence: "Built QA Sentinel API test runner and reports.", weight: 5, position: 0 },
        { criterion: "Weekly contractor availability", category: "experience", assessment: "unknown", evidence: "Confirm schedule in the offer call.", weight: 3, position: 1 },
      ],
      5: [
        { criterion: "Web development", category: "skill", assessment: "met", evidence: "Portfolio web tools and JavaScript UI work.", weight: 4, position: 0 },
      ],
      6: [
        { criterion: "SQL data validation", category: "skill", assessment: "met", evidence: "Used SQL checks in coursework and data projects.", weight: 5, position: 0 },
        { criterion: "Japanese communication", category: "language", assessment: "unknown", evidence: "Verify the team's working language.", weight: 2, position: 1 },
      ],
    };
    return (examples[applicationId] || []).map((requirement, index) => migrateRequirement(requirement, applicationId, index + 1));
  }

  function sampleEvents(application, migrated) {
    const base = application.id * 10;
    const eventAt = (daysAgo) => `${dateOffset(-daysAgo)}T09:00:00.000Z`;
    const event = (id, eventType, title, occurredAt, toStage, outcome = null, fromStage = null) => ({
      id,
      application_id: application.id,
      event_type: eventType,
      title,
      details: "Sample activity for the portfolio demo.",
      occurred_at: occurredAt,
      created_at: occurredAt,
      from_stage: fromStage,
      to_stage: toStage,
      origin: "system",
      payload_json: JSON.stringify({ from_stage: fromStage, to_stage: toStage, outcome }),
      request_id: null,
    });
    if (migrated.stage === "Wishlist") return [event(base, "custom", "Added to shortlist", eventAt(2), "Wishlist")];
    if (migrated.stage === "Applied") return [event(base, "applied", "Application submitted", eventAt(3), "Applied")];
    if (migrated.stage === "Interview") return [
      event(base, "applied", "Application submitted", eventAt(10), "Applied"),
      event(base + 1, "status_changed", "Interview scheduled", eventAt(6), "Interview", null, "Applied"),
    ];
    if (migrated.stage === "Offer") return [
      event(base, "applied", "Application submitted", eventAt(14), "Applied"),
      event(base + 1, "status_changed", "Interview completed", eventAt(10), "Interview", null, "Applied"),
      event(base + 2, "status_changed", "Offer received", eventAt(5), "Offer", null, "Interview"),
    ];
    return [
      event(base, "applied", "Application submitted", eventAt(25), "Applied"),
      event(base + 1, "status_changed", "Interview completed", eventAt(20), "Interview", null, "Applied"),
      event(base + 2, "status_changed", `Application ${migrated.outcome || "closed"}`, eventAt(16), "Closed", migrated.outcome || "Rejected", "Interview"),
    ];
  }

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
    return records.map((application) => {
      const migrated = migrateApplication(application);
      return {
        ...migrated,
        events: sampleEvents(application, migrated),
        tasks: migrated.tasks,
        requirements: sampleRequirements(application.id),
      };
    });
  }

  let corruptCacheRaw = null;

  function loadStored() {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw == null) return sampleApplications();
    try {
      const stored = JSON.parse(raw);
      if (stored && [2, 3, 4, 5, 6, 7, SCHEMA_VERSION].includes(stored.schema_version) && Array.isArray(stored.applications)) {
        return stored.applications.map((application) => {
          const migrated = migrateApplication(application);
          return { ...migrated, events: Array.isArray(application.events) ? application.events.map((event) => migrateEvent(event, migrated.id)) : [], tasks: migrated.tasks };
        });
      }
      corruptCacheRaw = raw;
    } catch (_error) {
      corruptCacheRaw = raw;
    }
    // A malformed demo cache should never make the public preview unusable;
    // the UI offers a raw download before the user chooses whether to reset it.
    return sampleApplications();
  }

  let applications = loadStored();
  window.JobFlowDemoHasCorruptCache = Boolean(corruptCacheRaw);
  window.JobFlowDemoRawCache = () => corruptCacheRaw;

  function commit(nextApplications) {
    const payload = { schema_version: SCHEMA_VERSION, saved_at: nowIso(), applications: nextApplications };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    corruptCacheRaw = null;
    window.JobFlowDemoHasCorruptCache = false;
    applications = nextApplications;
  }

  function makeError(message, fields = {}) {
    const notFound = /not found\.?$/i.test(String(message));
    const safeMessage = notFound ? `${String(message).replace(/\.?$/, "")} It may have been deleted or the link may be stale.` : message;
    const error = new Error(safeMessage);
    error.fields = fields;
    error.status = notFound ? 404 : undefined;
    error.code = notFound ? "NOT_FOUND" : (Object.keys(fields).length ? "VALIDATION_ERROR" : "REQUEST_FAILED");
    error.retryable = false;
    error.requestId = `demo-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return error;
  }

  function makeApiError(message, status, fields = {}, extra = {}) {
    const error = makeError(message, fields);
    error.status = status;
    error.code = extra.code || (status === 409 ? "CONFLICT" : status === 404 ? "NOT_FOUND" : status === 422 ? "VALIDATION_ERROR" : "REQUEST_FAILED");
    error.retryable = Boolean(extra.retryable || [408, 425, 429, 502, 503, 504].includes(status));
    Object.assign(error, extra);
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
    for (const field of ["company", "role", "work_mode"]) {
      cleaned[field] = String(cleaned[field] ?? "").trim();
      if (!cleaned[field]) errors[field] = "This field is required.";
    }
    const statusProvided = Object.prototype.hasOwnProperty.call(payload, "status");
    const stageProvided = Object.prototype.hasOwnProperty.call(payload, "stage");
    const rawStatus = statusProvided ? String(payload.status ?? "").trim() : "";
    const rawStage = stageProvided ? String(payload.stage ?? "").trim() : "";
    if (statusProvided && !statuses.includes(rawStatus)) errors.status = `Choose one of: ${statuses.join(", ")}.`;
    if (stageProvided && !stages.includes(rawStage)) errors.stage = `Choose one of: ${stages.join(", ")}.`;
    if (!existing && !rawStatus && !rawStage) errors.status = "This field is required.";
    if (statusProvided && stageProvided && statuses.includes(rawStatus) && stages.includes(rawStage) && legacyToStage[rawStatus] !== rawStage) {
      errors.stage = "Stage and status must describe the same lifecycle stage.";
    }
    const existingStage = existing?.stage && stages.includes(existing.stage) ? existing.stage : legacyToStage[existing?.status];
    const stage = stageProvided && stages.includes(rawStage)
      ? rawStage
      : statusProvided && statuses.includes(rawStatus)
        ? legacyToStage[rawStatus]
        : existingStage;
    if (stage) {
      cleaned.stage = stage;
      cleaned.status = stageToLegacy[stage];
    }
    for (const [field, maximum] of [["company", 120], ["role", 160], ["location", 120], ["source", 80], ["url", 500], ["notes", 4000]]) {
      cleaned[field] = String(cleaned[field] ?? "").trim();
      if (cleaned[field].length > maximum) errors[field] = `Must be ${maximum} characters or fewer.`;
    }
    if (!previewOptions.work_modes.includes(cleaned.work_mode)) errors.work_mode = `Choose one of: ${previewOptions.work_modes.join(", ")}.`;
    cleaned.currency = String(cleaned.currency || "USD").toUpperCase();
    cleaned.salary_period = String(cleaned.salary_period || "Annual");
    if (!previewOptions.currencies.includes(cleaned.currency)) errors.currency = `Choose one of: ${previewOptions.currencies.join(", ")}.`;
    if (!previewOptions.salary_periods.includes(cleaned.salary_period)) errors.salary_period = `Choose one of: ${previewOptions.salary_periods.join(", ")}.`;
    cleaned.salary_min = validateSalary(cleaned.salary_min, "salary_min", errors);
    cleaned.salary_max = validateSalary(cleaned.salary_max, "salary_max", errors);
    cleaned.applied_date = validateDate(cleaned.applied_date, "applied_date", errors);
    cleaned.next_action_date = validateDate(cleaned.next_action_date, "next_action_date", errors);
    cleaned.waiting_until = validateDate(cleaned.waiting_until, "waiting_until", errors);
    const rawOutcome = source.outcome == null ? null : String(source.outcome).trim() || null;
    cleaned.outcome = rawOutcome;
    if (rawOutcome && !outcomes.includes(rawOutcome)) errors.outcome = `Choose one of: ${outcomes.join(", ")}.`;
    const rawClosedAt = source.closed_at == null ? null : String(source.closed_at).trim() || null;
    cleaned.closed_at = rawClosedAt;
    if (rawClosedAt && Number.isNaN(Date.parse(rawClosedAt))) errors.closed_at = "Use an ISO date-time value.";
    const rawVersion = source.version == null ? 1 : source.version;
    if (!Number.isInteger(rawVersion) || rawVersion < 1) errors.version = "Version must be a positive integer.";
    cleaned.version = Number.isInteger(rawVersion) && rawVersion > 0 ? rawVersion : 1;
    if (cleaned.stage === "Closed") {
      if (!cleaned.outcome && (rawStatus === "Rejected" || cleaned.status === "Rejected") && !Object.prototype.hasOwnProperty.call(payload, "outcome")) cleaned.outcome = "Rejected";
      if (!cleaned.outcome) errors.outcome = "Closed applications require an outcome.";
      if (!cleaned.closed_at) {
        if ((rawStatus === "Rejected" || cleaned.status === "Rejected") && !stageProvided && !Object.prototype.hasOwnProperty.call(payload, "closed_at")) {
          cleaned.closed_at = nowIso();
        } else {
          errors.closed_at = "Closed applications require a closed-at timestamp.";
        }
      }
    } else {
      if (cleaned.outcome) errors.outcome = "Only Closed applications can have an outcome.";
      if (cleaned.closed_at) errors.closed_at = "Only Closed applications can have a closed-at timestamp.";
      cleaned.outcome = null;
      cleaned.closed_at = null;
    }
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
      from_stage: payload.from_stage && stages.includes(String(payload.from_stage)) ? String(payload.from_stage) : null,
      to_stage: payload.to_stage && stages.includes(String(payload.to_stage)) ? String(payload.to_stage) : null,
      origin: eventOrigins.includes(payload.origin) ? payload.origin : "user",
      payload_json: payload.payload_json && typeof payload.payload_json === "object" && !Array.isArray(payload.payload_json) ? payload.payload_json : {},
      request_id: payload.request_id ? String(payload.request_id).trim() : null,
    };
    const errors = {};
    if (!eventTypes.includes(event.event_type)) errors.event_type = `Choose one of: ${eventTypes.join(", ")}.`;
    if (!event.title) errors.title = "This field is required.";
    if (event.title.length > 160) errors.title = "Must be 160 characters or fewer.";
    if (event.details.length > 4000) errors.details = "Must be 4000 characters or fewer.";
    if (Number.isNaN(Date.parse(event.occurred_at))) errors.occurred_at = "Use an ISO date-time value.";
    if (payload.from_stage && !event.from_stage) errors.from_stage = `Choose one of: ${stages.join(", ")}.`;
    if (payload.to_stage && !event.to_stage) errors.to_stage = `Choose one of: ${stages.join(", ")}.`;
    if (payload.origin && !eventOrigins.includes(payload.origin)) errors.origin = `Choose one of: ${eventOrigins.join(", ")}.`;
    if (event.request_id && event.request_id.length > 128) errors.request_id = "Request ID must be 128 characters or fewer.";
    if (Object.keys(errors).length) throw makeError("Validation failed.", errors);
    return event;
  }

  function validateTask(payload, { partial = false } = {}) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw makeError("Validation failed.", { body: "Expected an object." });
    const allowed = new Set(["kind", "title", "due_date", "completed_at", "version", "expected_version"]);
    const errors = {};
    Object.keys(payload).filter((field) => !allowed.has(field)).forEach((field) => { errors.body = `Unknown fields: ${field}.`; });
    const task = {};
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "kind")) {
      task.kind = String(payload.kind || "").trim();
      if (!taskKinds.includes(task.kind)) errors.kind = `Choose one of: ${taskKinds.join(", ")}.`;
    }
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "title")) {
      task.title = String(payload.title || "").trim();
      if (!task.title) errors.title = "This field is required.";
      if (task.title.length > 200) errors.title = "Must be 200 characters or fewer.";
    }
    if (Object.prototype.hasOwnProperty.call(payload, "due_date")) {
      task.due_date = validateDate(payload.due_date, "due_date", errors);
    } else if (!partial) {
      errors.due_date = "This field is required.";
    }
    if (Object.prototype.hasOwnProperty.call(payload, "completed_at")) {
      task.completed_at = payload.completed_at == null || payload.completed_at === "" ? null : String(payload.completed_at);
      if (task.completed_at && Number.isNaN(Date.parse(task.completed_at))) errors.completed_at = "Use an ISO date-time value.";
    } else if (!partial) {
      task.completed_at = null;
    }
    for (const field of ["version", "expected_version"]) {
      if (Object.prototype.hasOwnProperty.call(payload, field)) {
        if (!Number.isInteger(payload[field]) || payload[field] < 1) errors[field] = "Version must be a positive integer.";
        else task[field] = payload[field];
      }
    }
    if (!partial) task.version = task.version || 1;
    if (Object.keys(errors).length) throw makeError("Validation failed.", errors);
    return task;
  }

  function validateRequirement(payload, { partial = false } = {}) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw makeError("Validation failed.", { body: "Expected an object." });
    const allowed = new Set(["criterion", "category", "assessment", "evidence", "weight", "position"]);
    const errors = {};
    Object.keys(payload).filter((field) => !allowed.has(field)).forEach((field) => { errors.body = `Unknown fields: ${field}.`; });
    const requirement = {};
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "criterion")) {
      requirement.criterion = String(payload.criterion || "").trim();
      if (!requirement.criterion) errors.criterion = "This field is required.";
      if (requirement.criterion.length > 240) errors.criterion = "Must be 240 characters or fewer.";
    }
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "category")) {
      requirement.category = String(payload.category || "").trim();
      if (!requirementCategories.includes(requirement.category)) errors.category = `Choose one of: ${requirementCategories.join(", ")}.`;
    }
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "assessment")) {
      requirement.assessment = String(payload.assessment || "unknown").trim();
      if (!requirementAssessments.includes(requirement.assessment)) errors.assessment = `Choose one of: ${requirementAssessments.join(", ")}.`;
    }
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "evidence")) {
      requirement.evidence = String(payload.evidence || "").trim();
      if (requirement.evidence.length > 2000) errors.evidence = "Must be 2000 characters or fewer.";
    }
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "weight")) {
      requirement.weight = payload.weight == null ? 1 : payload.weight;
      if (!Number.isInteger(requirement.weight) || requirement.weight < 1 || requirement.weight > 5) errors.weight = "Weight must be an integer from 1 to 5.";
    }
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "position")) {
      requirement.position = payload.position == null ? 0 : payload.position;
      if (!Number.isInteger(requirement.position) || requirement.position < 0) errors.position = "Position must be a non-negative integer.";
    }
    if (Object.keys(errors).length) throw makeError("Validation failed.", errors);
    return requirement;
  }

  function validateArtifact(payload, { partial = false } = {}) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw makeError("Validation failed.", { body: "Expected an object." });
    const allowed = new Set(["kind", "label", "uri", "version_label", "notes"]);
    const errors = {};
    Object.keys(payload).filter((field) => !allowed.has(field)).forEach((field) => { errors.body = `Unknown fields: ${field}.`; });
    const artifact = {};
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "kind")) {
      artifact.kind = String(payload.kind || "").trim();
      if (!artifactKinds.includes(artifact.kind)) errors.kind = `Choose one of: ${artifactKinds.join(", ")}.`;
    }
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "label")) {
      artifact.label = String(payload.label || "").trim();
      if (!artifact.label) errors.label = "This field is required.";
      if (artifact.label.length > 160) errors.label = "Must be 160 characters or fewer.";
    }
    if (!partial || Object.prototype.hasOwnProperty.call(payload, "uri")) {
      artifact.uri = String(payload.uri || "").trim();
      if (artifact.uri.length > 500) errors.uri = "Must be 500 characters or fewer.";
      if (artifact.uri) {
        try {
          const parsed = new URL(artifact.uri);
          if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) throw new Error();
        } catch (_error) {
          errors.uri = "Enter a complete http:// or https:// URL.";
        }
      }
    }
    for (const [field, maximum] of [["version_label", 80], ["notes", 2000]]) {
      if (!partial || Object.prototype.hasOwnProperty.call(payload, field)) {
        artifact[field] = String(payload[field] || "").trim();
        if (artifact[field].length > maximum) errors[field] = `Must be ${maximum} characters or fewer.`;
      }
    }
    if (Object.keys(errors).length) throw makeError("Validation failed.", errors);
    return artifact;
  }

  function validateSubmission(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw makeError("Validation failed.", { body: "Expected an object." });
    const errors = {};
    const artifactIds = payload.artifact_ids;
    if (!Array.isArray(artifactIds) || !artifactIds.length) errors.artifact_ids = "Choose at least one material.";
    if (Array.isArray(artifactIds) && (artifactIds.length > 100 || artifactIds.some((id) => !Number.isInteger(id) || id < 1))) errors.artifact_ids = "Material IDs must be positive integers.";
    if (Array.isArray(artifactIds) && new Set(artifactIds).size !== artifactIds.length) errors.artifact_ids = "A material can only be selected once.";
    const notes = String(payload.notes || "").trim();
    if (notes.length > 2000) errors.notes = "Must be 2000 characters or fewer.";
    let submittedAt = payload.submitted_at == null || payload.submitted_at === "" ? null : String(payload.submitted_at);
    if (submittedAt && Number.isNaN(Date.parse(submittedAt))) errors.submitted_at = "Use an ISO date-time value.";
    if (Object.keys(errors).length) throw makeError("Validation failed.", errors);
    return { artifact_ids: artifactIds, notes, submitted_at: submittedAt };
  }

  function addEvent(application, event) {
    const nextId = applications.flatMap((item) => item.events || []).reduce((maximum, item) => Math.max(maximum, item.id || 0), 0) + 1;
    application.events = [...(application.events || []), {
      id: nextId,
      application_id: application.id,
      from_stage: null,
      to_stage: null,
      origin: "system",
      payload_json: "{}",
      request_id: null,
      ...event,
      payload_json: typeof event.payload_json === "string" ? event.payload_json : JSON.stringify(event.payload_json || {}),
      created_at: nowIso(),
    }];
  }

  function validateTransition(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw makeApiError("Validation failed.", 422, { body: "Expected an object." });
    const errors = {};
    const toStage = String(payload.to_stage || "").trim();
    if (!stages.includes(toStage)) errors.to_stage = `Choose one of: ${stages.join(", ")}.`;
    const outcome = payload.outcome == null || String(payload.outcome).trim() === "" ? null : String(payload.outcome).trim();
    if (outcome && !outcomes.includes(outcome)) errors.outcome = `Choose one of: ${outcomes.join(", ")}.`;
    if (toStage === "Closed" && !outcome) errors.outcome = "Closing an application requires an outcome.";
    if (toStage !== "Closed" && outcome) errors.outcome = "Only Closed applications can have an outcome.";
    let occurredAt = payload.occurred_at ? String(payload.occurred_at) : nowIso();
    if (Number.isNaN(Date.parse(occurredAt))) errors.occurred_at = "Use an ISO date-time value.";
    const expectedVersion = payload.expected_version == null ? null : payload.expected_version;
    if (expectedVersion != null && (!Number.isInteger(expectedVersion) || expectedVersion < 1)) errors.expected_version = "Expected version must be a positive integer.";
    const requestId = payload.request_id == null ? null : String(payload.request_id).trim();
    if (requestId != null && (!requestId || requestId.length > 128)) errors.request_id = "Request ID must be 1–128 characters.";
    if (Object.keys(errors).length) throw makeApiError("Validation failed.", 422, errors);
    return { to_stage: toStage, outcome, occurred_at: occurredAt, expected_version: expectedVersion, request_id: requestId };
  }

  function list(query) {
    const search = (query.get("search") || "").toLowerCase();
    const status = query.get("status") || "";
    const stage = query.get("stage") || "";
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
      return viewMatch && (!search || haystack.includes(search)) && (!status || application.status === status) && (!stage || application.stage === stage) && (!workMode || application.work_mode === workMode);
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

  function parseEventDate(value) {
    if (value == null || value === "") return null;
    const timestamp = Date.parse(String(value));
    return Number.isNaN(timestamp) ? null : timestamp;
  }

  function medianDays(values) {
    if (!values.length) return null;
    const sorted = values.slice().sort((left, right) => left - right);
    const middle = Math.floor(sorted.length / 2);
    const value = sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    return Math.round(value * 10) / 10;
  }

  function historicalInsights(windowName = "all") {
    if (!["30", "90", "all"].includes(windowName)) throw makeError("Invalid insights parameters.", { window: "Choose 30, 90, or all." });
    const now = Date.now();
    const cutoff = windowName === "all" ? null : now - Number(windowName) * 86400000;
    const stageNames = new Set(stages);
    const histories = applications.map((application) => {
      const rawEvents = Array.isArray(application.events) ? application.events : [];
      let limited = rawEvents.some((event) => (event.origin || "") === "legacy");
      const staged = [];
      rawEvents.forEach((event) => {
        const occurredAt = parseEventDate(event.occurred_at);
        if (occurredAt == null) {
          limited = true;
          return;
        }
        let stage = stageNames.has(event.to_stage) ? event.to_stage : null;
        let outcome = null;
        let payload = event.payload_json;
        if (typeof payload === "string") {
          try { payload = JSON.parse(payload); } catch (_error) { payload = null; }
        }
        if (payload && typeof payload === "object" && !Array.isArray(payload)) {
          if (!stage && stageNames.has(payload.to_stage)) stage = payload.to_stage;
          outcome = typeof payload.outcome === "string" ? payload.outcome : null;
        }
        if (event.event_type === "applied") stage = "Applied";
        if (stage) staged.push({ occurredAt, stage, outcome });
      });
      staged.sort((left, right) => left.occurredAt - right.occurredAt);
      if (!staged.length && application.stage !== "Wishlist") limited = true;
      const submittedIndex = staged.findIndex((event) => event.stage === "Applied");
      const submitted = submittedIndex >= 0 ? staged[submittedIndex] : null;
      if (!submitted) return { application, submittedAt: null, limited };
      const postSubmitted = staged.slice(submittedIndex);
      if (![null, undefined, "Wishlist", "Ready", "Applied"].includes(application.stage) && !postSubmitted.some((event) => event.stage === application.stage)) limited = true;
      let respondedAt = null;
      let interviewed = false;
      let offered = false;
      let accepted = false;
      postSubmitted.forEach((event) => {
        if (!respondedAt && ["Interview", "Offer", "Closed"].includes(event.stage)) respondedAt = event.occurredAt;
        interviewed = interviewed || event.stage === "Interview";
        offered = offered || event.stage === "Offer";
        accepted = accepted || (event.stage === "Closed" && event.outcome === "Accepted");
      });
      const stageDurations = {};
      for (let index = 0; index < postSubmitted.length - 1; index += 1) {
        const start = postSubmitted[index];
        const end = postSubmitted[index + 1];
        if (end.occurredAt <= start.occurredAt || start.stage === end.stage) continue;
        if (["Wishlist", "Ready", "Closed"].includes(start.stage) || ["Wishlist", "Ready"].includes(end.stage)) continue;
        stageDurations[start.stage] = stageDurations[start.stage] || [];
        stageDurations[start.stage].push((end.occurredAt - start.occurredAt) / 86400000);
      }
      return { application, submittedAt: submitted.occurredAt, respondedAt, interviewed, offered, accepted, stageDurations, limited };
    });
    const cohort = histories.filter((item) => item.submittedAt != null && (cutoff == null || (item.submittedAt >= cutoff && item.submittedAt <= now)));
    const submitted = cohort.length;
    const respondedItems = cohort.filter((item) => item.respondedAt != null);
    const interviewed = cohort.filter((item) => item.interviewed).length;
    const offered = cohort.filter((item) => item.offered).length;
    const accepted = cohort.filter((item) => item.accepted).length;
    const percentage = (numerator, denominator) => denominator ? Math.round((numerator / denominator) * 1000) / 10 : null;
    const responseTimes = respondedItems
      .filter((item) => item.respondedAt >= item.submittedAt)
      .map((item) => (item.respondedAt - item.submittedAt) / 86400000);
    const durationValues = {};
    cohort.forEach((item) => Object.entries(item.stageDurations || {}).forEach(([stage, values]) => {
      durationValues[stage] = (durationValues[stage] || []).concat(values);
    }));
    const sourceGroups = new Map();
    cohort.forEach((item) => {
      const source = String(item.application.source || "").trim() || "Unknown";
      if (!sourceGroups.has(source)) sourceGroups.set(source, []);
      sourceGroups.get(source).push(item);
    });
    const sourceConversion = [...sourceGroups.keys()].sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" })).map((source) => {
      const group = sourceGroups.get(source);
      const sourceResponded = group.filter((item) => item.respondedAt != null).length;
      const sourceInterviewed = group.filter((item) => item.interviewed).length;
      const sourceOffered = group.filter((item) => item.offered).length;
      const sourceAccepted = group.filter((item) => item.accepted).length;
      return {
        source,
        submitted: group.length,
        responded: sourceResponded,
        interviewed: sourceInterviewed,
        offered: sourceOffered,
        accepted: sourceAccepted,
        response_rate: percentage(sourceResponded, group.length),
        interview_rate: percentage(sourceInterviewed, group.length),
        offer_rate: percentage(sourceOffered, group.length),
        acceptance_rate: percentage(sourceAccepted, group.length),
      };
    });
    const submittedTimes = cohort.map((item) => item.submittedAt);
    const cohortStart = submittedTimes.length ? new Date(Math.min(...submittedTimes)).toISOString() : null;
    const cohortEnd = submittedTimes.length ? new Date(Math.max(...submittedTimes)).toISOString() : null;
    const limited = cohort.filter((item) => item.limited).length;
    const limitedTotal = histories.filter((item) => item.limited).length;
    return {
      window: windowName,
      cohort: { start: cohortStart, end: cohortEnd, submitted },
      cohort_start: cohortStart,
      cohort_end: cohortEnd,
      submitted,
      responded: respondedItems.length,
      interviewed,
      offered,
      accepted,
      no_response: submitted - respondedItems.length,
      response_rate: percentage(respondedItems.length, submitted),
      interview_rate: percentage(interviewed, submitted),
      offer_rate: percentage(offered, submitted),
      acceptance_rate: percentage(accepted, submitted),
      median_time_to_response: medianDays(responseTimes),
      median_time_in_stage: Object.fromEntries(Object.entries(durationValues).sort(([left], [right]) => left.localeCompare(right)).map(([stage, values]) => [stage, medianDays(values)])),
      source_conversion: sourceConversion,
      history_quality: {
        complete: submitted - limited,
        limited,
        limited_total: limitedTotal,
        limited_note: "Legacy or incomplete event history is excluded from inferred transitions; missing timestamps are not invented.",
      },
      denominators: {
        submitted: "Applications with a first recorded Applied transition in the selected window.",
        responded: "First transition out of Applied divided by the submitted cohort.",
        interviewed: "Applications that ever reached Interview divided by the submitted cohort.",
        offered: "Applications that ever reached Offer divided by the submitted cohort.",
        accepted: "Applications that reached Closed with outcome Accepted divided by the submitted cohort.",
        no_response: "Submitted applications with no recorded transition to Interview, Offer, or Closed.",
        source_conversion: "Each source uses its first-submitted application count in the selected cohort as denominator.",
        time_metrics: "Completed, timestamped intervals only; current open intervals are not estimated.",
      },
    };
  }

  function today(asOf = todayIso()) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf) || Number.isNaN(asDate(asOf))) throw makeError("Invalid query parameters.", { as_of: "Use ISO format YYYY-MM-DD." });
    const activeStages = new Set(["Ready", "Applied", "Interview", "Offer"]);
    const openTasks = applications.flatMap((application) => (application.tasks || []).filter((task) => !task.completed_at && application.stage !== "Closed").map((task) => ({ ...task, company: application.company, role: application.role, stage: application.stage, status: application.status })));
    openTasks.sort((left, right) => left.due_date.localeCompare(right.due_date) || left.id - right.id);
    const result = { overdue: [], due_today: [], upcoming: [], waiting: [], missing_next_step: [], as_of: asOf };
    openTasks.forEach((task) => {
      if (task.due_date < asOf) result.overdue.push(task);
      else if (task.due_date === asOf) result.due_today.push(task);
      else result.upcoming.push(task);
    });
    applications.filter((application) => activeStages.has(application.stage) && application.waiting_until && application.waiting_until > asOf)
      .sort((left, right) => left.waiting_until.localeCompare(right.waiting_until) || left.id - right.id)
      .forEach((application) => result.waiting.push(application));
    applications.filter((application) => activeStages.has(application.stage) && !application.waiting_until && !(application.tasks || []).some((task) => !task.completed_at))
      .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")) || right.id - left.id)
      .forEach((application) => result.missing_next_step.push(application));
    return result;
  }

  const parseBody = (options) => {
    try { return options.body ? JSON.parse(options.body) : {}; }
    catch (_error) { throw makeError("Request body must contain valid JSON."); }
  };

  function previewDuplicates(records) {
    const csv = window.JobFlowCsv;
    if (!csv) return [];
    const conflicts = csv.findDuplicateMatches(records, applications);
    const byIncoming = new Set(conflicts.map((item) => item.incoming_index));
    records.forEach((record, incomingIndex) => {
      if (byIncoming.has(incomingIndex)) return;
      for (let previousIndex = 0; previousIndex < incomingIndex; previousIndex += 1) {
        const reason = csv.duplicateReason(record, records[previousIndex]);
        if (reason) {
          conflicts.push({
            incoming_index: incomingIndex,
            matched_incoming_index: previousIndex,
            existing_application_id: null,
            reason,
            fingerprint: csv.applicationFingerprint(record),
            incoming: record,
            existing: records[previousIndex],
          });
          break;
        }
      }
    });
    return conflicts.map((conflict) => {
      const existing = conflict.existing_application_id == null ? null : applications.find((item) => item.id === conflict.existing_application_id);
      return { ...conflict, incoming: records[conflict.incoming_index], existing };
    });
  }

  window.JobFlowDemoReset = () => commit(sampleApplications());

  window.JobFlowDemoApi = async (path, options = {}) => {
    const request = new URL(path, "https://jobflow.preview");
    const method = (options.method || "GET").toUpperCase();
    if (request.pathname === "/api/meta/options" && method === "GET") return copy(previewOptions);
    if (request.pathname === "/api/analytics" && method === "GET") return copy(analytics());
    if (request.pathname === "/api/insights" && method === "GET") {
      for (const key of request.searchParams.keys()) if (key !== "window") throw makeError("Invalid insights parameters.", { query: `Unknown parameters: ${key}.` });
      return copy(historicalInsights(request.searchParams.get("window") || "all"));
    }
    if (request.pathname === "/api/today" && method === "GET") {
      for (const key of request.searchParams.keys()) if (key !== "as_of") throw makeError("Invalid query parameters.", { query: `Unknown parameters: ${key}.` });
      return copy(today(request.searchParams.get("as_of") || todayIso()));
    }
    const workspaceMatch = request.pathname.match(/^\/api\/applications\/(\d+)\/workspace$/);
    if (workspaceMatch && method === "GET") {
      const application = applications.find((item) => item.id === Number(workspaceMatch[1]));
      if (!application) throw makeError("Application not found.");
      const tasks = (application.tasks || []).slice().sort((left, right) => (left.completed_at ? 1 : 0) - (right.completed_at ? 1 : 0) || left.due_date.localeCompare(right.due_date) || left.id - right.id);
      const openTasks = tasks.filter((task) => !task.completed_at);
      const completedTasks = tasks.filter((task) => task.completed_at);
      const events = (application.events || []).slice().sort((left, right) => String(right.occurred_at || "").localeCompare(String(left.occurred_at || "")) || right.id - left.id);
      const requirements = (application.requirements || []).slice().sort((left, right) => left.position - right.position || left.id - right.id);
      const artifacts = (application.artifacts || []).slice().sort((left, right) => String(left.created_at || "").localeCompare(String(right.created_at || "")) || left.id - right.id);
      const submissions = (application.submissions || []).slice().sort((left, right) => String(right.submitted_at || "").localeCompare(String(left.submitted_at || "")) || right.id - left.id);
      return copy({
        application,
        open_tasks: openTasks,
        completed_tasks: completedTasks,
        events,
        requirements,
        requirement_summary: summarizeRequirements(requirements),
        artifacts,
        submissions,
        summary: {
          open_tasks: openTasks.length,
          completed_tasks: completedTasks.length,
          activity_count: events.length,
          next_task: openTasks[0] || null,
        },
      });
    }
    const requirementsMatch = request.pathname.match(/^\/api\/applications\/(\d+)\/requirements$/);
    if (requirementsMatch && method === "GET") {
      const application = applications.find((item) => item.id === Number(requirementsMatch[1]));
      if (!application) throw makeError("Application not found.");
      return copy((application.requirements || []).slice().sort((left, right) => left.position - right.position || left.id - right.id));
    }
    if (requirementsMatch && method === "POST") {
      const application = applications.find((item) => item.id === Number(requirementsMatch[1]));
      if (!application) throw makeError("Application not found.");
      const requirement = validateRequirement(parseBody(options));
      const next = copy(applications);
      const target = next.find((item) => item.id === application.id);
      target.requirements = target.requirements || [];
      const nextId = next.flatMap((item) => item.requirements || []).reduce((maximum, item) => Math.max(maximum, item.id || 0), 0) + 1;
      const record = { id: nextId, application_id: target.id, ...requirement, created_at: nowIso(), updated_at: nowIso() };
      target.requirements.push(record);
      commit(next);
      return copy(record);
    }
    if (requirementsMatch && method === "PUT") {
      const application = applications.find((item) => item.id === Number(requirementsMatch[1]));
      if (!application) throw makeError("Application not found.");
      const payload = parseBody(options);
      const orderedIds = payload && payload.ordered_ids;
      if (!payload || typeof payload !== "object" || Array.isArray(payload) || Object.keys(payload).length !== 1 || !Array.isArray(orderedIds)) {
        throw makeApiError("Validation failed.", 422, { body: "Provide only an ordered_ids array." });
      }
      if (orderedIds.some((id) => !Number.isInteger(id) || id < 1)) {
        throw makeApiError("Validation failed.", 422, { ordered_ids: "Expected an array of positive integer IDs." });
      }
      const currentIds = (application.requirements || []).map((requirement) => requirement.id);
      if (orderedIds.length !== currentIds.length || new Set(orderedIds).size !== currentIds.length || orderedIds.some((id) => !currentIds.includes(id))) {
        throw makeApiError("Validation failed.", 422, { ordered_ids: "ordered_ids must contain each application requirement exactly once." });
      }
      const next = copy(applications);
      const target = next.find((item) => item.id === application.id);
      const byId = new Map(orderedIds.map((id, position) => [id, position]));
      target.requirements.forEach((requirement) => { requirement.position = byId.get(requirement.id); requirement.updated_at = nowIso(); });
      target.requirements.sort((left, right) => left.position - right.position || left.id - right.id);
      commit(next);
      return copy(target.requirements);
    }
    const artifactsMatch = request.pathname.match(/^\/api\/applications\/(\d+)\/artifacts$/);
    if (artifactsMatch && method === "GET") {
      const application = applications.find((item) => item.id === Number(artifactsMatch[1]));
      if (!application) throw makeError("Application not found.");
      return copy((application.artifacts || []).slice().sort((left, right) => String(left.created_at || "").localeCompare(String(right.created_at || "")) || left.id - right.id));
    }
    if (artifactsMatch && method === "POST") {
      const application = applications.find((item) => item.id === Number(artifactsMatch[1]));
      if (!application) throw makeError("Application not found.");
      const artifact = validateArtifact(parseBody(options));
      const next = copy(applications);
      const target = next.find((item) => item.id === application.id);
      target.artifacts = target.artifacts || [];
      const nextId = next.flatMap((item) => item.artifacts || []).reduce((maximum, item) => Math.max(maximum, item.id || 0), 0) + 1;
      const record = { id: nextId, application_id: target.id, ...artifact, created_at: nowIso(), updated_at: nowIso() };
      target.artifacts.push(record);
      commit(next);
      return copy(record);
    }
    const submissionsMatch = request.pathname.match(/^\/api\/applications\/(\d+)\/submissions$/);
    if (submissionsMatch && method === "GET") {
      const application = applications.find((item) => item.id === Number(submissionsMatch[1]));
      if (!application) throw makeError("Application not found.");
      return copy((application.submissions || []).slice().sort((left, right) => String(right.submitted_at || "").localeCompare(String(left.submitted_at || "")) || right.id - left.id));
    }
    if (submissionsMatch && method === "POST") {
      const application = applications.find((item) => item.id === Number(submissionsMatch[1]));
      if (!application) throw makeError("Application not found.");
      const submission = validateSubmission(parseBody(options));
      const artifactsById = new Map((application.artifacts || []).map((artifact) => [artifact.id, artifact]));
      if (submission.artifact_ids.some((id) => !artifactsById.has(id))) throw makeApiError("Every selected material must belong to this application.", 422, { artifact_ids: "Every selected material must belong to this application." }, { code: "INVALID_SUBMISSION" });
      const next = copy(applications);
      const target = next.find((item) => item.id === application.id);
      target.submissions = target.submissions || [];
      const nextId = next.flatMap((item) => item.submissions || []).reduce((maximum, item) => Math.max(maximum, item.id || 0), 0) + 1;
      const submittedAt = submission.submitted_at || nowIso();
      const targetArtifacts = new Map((target.artifacts || []).map((artifact) => [artifact.id, artifact]));
      const record = {
        id: nextId, application_id: target.id, submitted_at: submittedAt, notes: submission.notes,
        created_at: nowIso(),
        items: submission.artifact_ids.map((artifactId, position) => {
          const artifact = targetArtifacts.get(artifactId);
          return { package_id: nextId, artifact_id: artifactId, position, snapshot_kind: artifact.kind, snapshot_label: artifact.label, snapshot_uri: artifact.uri, snapshot_version_label: artifact.version_label, snapshot_notes: artifact.notes };
        }),
      };
      target.submissions.push(record);
      commit(next);
      return copy(record);
    }
    const submissionIdMatch = request.pathname.match(/^\/api\/submissions\/(\d+)$/);
    if (submissionIdMatch && method === "GET") {
      const submissionId = Number(submissionIdMatch[1]);
      const submission = applications.flatMap((item) => item.submissions || []).find((item) => item.id === submissionId);
      if (!submission) throw makeError("Submission not found.");
      return copy(submission);
    }
    if (request.pathname === "/api/export" && method === "GET") return copy({ schema_version: SCHEMA_VERSION, exported_at: nowIso(), applications });
    if (request.pathname === "/api/import/preview" && method === "POST") {
      const payload = parseBody(options);
      if (!payload || !Array.isArray(payload.applications)) throw makeApiError("Import preview failed.", 422, { body: "Expected an object with an applications array." });
      if (payload.applications.length > 5000) throw makeApiError("Import preview failed.", 422, { applications: "A backup can contain at most 5,000 records." });
      const valid = [];
      const invalid = [];
      payload.applications.forEach((record, index) => {
        try { valid.push(validateApplication(record)); }
        catch (error) { invalid.push({ incoming_index: index, errors: error.fields || { body: error.message } }); }
      });
      return { valid_count: valid.length, valid_records: valid, invalid, conflicts: previewDuplicates(valid) };
    }
    if (request.pathname === "/api/applications" && method === "GET") return copy(list(request.searchParams));
    if (request.pathname === "/api/applications" && method === "POST") {
      const cleaned = validateApplication(parseBody(options));
      const nextId = applications.reduce((maximum, application) => Math.max(maximum, application.id), 0) + 1;
      const timestamp = nowIso();
      const application = { id: nextId, ...cleaned, created_at: timestamp, updated_at: timestamp, events: [], tasks: [], requirements: [], artifacts: [], submissions: [] };
      if (cleaned.next_action_date) application.tasks.push(migrateTask({ kind: "follow_up", title: "Follow up", due_date: cleaned.next_action_date }, nextId, 1));
      syncNextActionDate(application);
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
      const conflicts = mode === "append" ? previewDuplicates(cleaned) : [];
      const decisions = Array.isArray(payload.duplicate_decisions) ? payload.duplicate_decisions : [];
      const decisionsByIndex = new Map(decisions.map((decision) => [decision.incoming_index, decision]));
      if (decisions.length !== decisionsByIndex.size || decisions.some((decision) => !conflicts.some((conflict) => conflict.incoming_index === decision.incoming_index))) {
        throw makeApiError("Import validation failed.", 422, { duplicate_decisions: "Each decision must refer to one conflicting row." });
      }
      if (decisions.some((decision) => !["create", "separate", "skip", "merge"].includes(decision.action))) {
        throw makeApiError("Import validation failed.", 422, { duplicate_decisions: "Choose create, separate, skip, or merge." });
      }
      const missing = conflicts.filter((conflict) => !decisionsByIndex.has(conflict.incoming_index));
      if (missing.length) throw makeApiError("Duplicate applications need a decision before import.", 409, {}, { code: "DUPLICATES_FOUND", conflicts });
      const skipped = cleaned.filter((_record, index) => decisionsByIndex.get(index)?.action === "skip").length;
      const nextApplications = copy(applications);
      cleaned.forEach((record, index) => {
        const decision = decisionsByIndex.get(index);
        if (!decision || decision.action !== "merge") return;
        const conflict = conflicts.find((item) => item.incoming_index === index);
        const target = nextApplications.find((item) => item.id === conflict?.existing_application_id);
        if (!target || !Number.isInteger(decision.existing_application_id) || decision.existing_application_id !== target.id || !Array.isArray(decision.fields)) {
          throw makeApiError("Import validation failed.", 422, { duplicate_decisions: "Merge requires an existing application and selected fields." });
        }
        decision.fields.forEach((field) => { if (record[field] !== null && record[field] !== "" && IMPORT_MERGE_FIELDS.includes(field)) target[field] = record[field]; });
        target.version = (target.version || 1) + 1;
        target.updated_at = nowIso();
      });
      const merged = cleaned.filter((_record, index) => decisionsByIndex.get(index)?.action === "merge").length;
      let nextId = mode === "replace" ? 1 : applications.reduce((maximum, application) => Math.max(maximum, application.id), 0) + 1;
      let nextArtifactId = mode === "replace" ? 1 : applications.flatMap((application) => application.artifacts || []).reduce((maximum, artifact) => Math.max(maximum, artifact.id || 0), 0) + 1;
      let nextSubmissionId = mode === "replace" ? 1 : applications.flatMap((application) => application.submissions || []).reduce((maximum, submission) => Math.max(maximum, submission.id || 0), 0) + 1;
      const timestamp = nowIso();
      const imported = cleaned.filter((_record, index) => !["skip", "merge"].includes(decisionsByIndex.get(index)?.action)).map((record, index) => {
        const originalIndex = cleaned.indexOf(record);
        const applicationId = nextId++;
        const rawApplication = payload.applications[originalIndex];
        const artifactIdMap = new Map();
        const importedArtifacts = Array.isArray(rawApplication.artifacts)
          ? rawApplication.artifacts.map((artifact, artifactIndex) => {
            const newId = nextArtifactId++;
            artifactIdMap.set(artifact.id, newId);
            return { id: newId, application_id: applicationId, ...migrateArtifact(validateArtifact({ kind: artifact.kind, label: artifact.label, uri: artifact.uri, version_label: artifact.version_label, notes: artifact.notes }), applicationId, artifactIndex + 1), created_at: timestamp, updated_at: timestamp };
          })
          : [];
        const importedSubmissions = Array.isArray(rawApplication.submissions)
          ? rawApplication.submissions.map((submission) => {
            const packageId = nextSubmissionId++;
            const items = (Array.isArray(submission.items) ? submission.items : []).map((item, itemIndex) => {
              const artifactId = artifactIdMap.get(item.artifact_id);
              if (!artifactId) throw makeError("Import validation failed.", { submissions: "A submission references an unknown material." });
              return { package_id: packageId, artifact_id: artifactId, position: itemIndex, snapshot_kind: item.snapshot_kind, snapshot_label: item.snapshot_label, snapshot_uri: item.snapshot_uri, snapshot_version_label: item.snapshot_version_label, snapshot_notes: item.snapshot_notes };
            });
            return { id: packageId, application_id: applicationId, submitted_at: submission.submitted_at || timestamp, notes: String(submission.notes || "").trim(), created_at: timestamp, items };
          })
          : [];
        return {
          id: applicationId,
          ...record,
          created_at: timestamp,
          updated_at: timestamp,
          tasks: Array.isArray(rawApplication.tasks)
            ? rawApplication.tasks.map((task, taskIndex) => migrateTask(validateTask({ kind: task.kind, title: task.title, due_date: task.due_date, completed_at: task.completed_at, version: task.version }), applicationId, taskIndex + 1))
            : [],
          requirements: Array.isArray(rawApplication.requirements)
            ? rawApplication.requirements.map((requirement, requirementIndex) => migrateRequirement(validateRequirement({ criterion: requirement.criterion, category: requirement.category, assessment: requirement.assessment, evidence: requirement.evidence, weight: requirement.weight, position: requirement.position }), applicationId, requirementIndex + 1))
            : [],
          artifacts: importedArtifacts,
          submissions: importedSubmissions,
          events: Array.isArray(rawApplication.events)
            ? rawApplication.events.map((event) => ({ ...validateEvent(event), origin: "import", request_id: null })).map((event, eventIndex) => ({ id: eventIndex + 1, application_id: applicationId, ...event, created_at: timestamp }))
            : [],
        };
      }).map((application) => {
        if (!application.tasks.length && application.next_action_date && application.stage !== "Closed") application.tasks.push(migrateTask({ kind: "follow_up", title: "Follow up", due_date: application.next_action_date }, application.id, 1));
        application.requirements = application.requirements || [];
        syncNextActionDate(application);
        return application;
      });
      commit(mode === "replace" ? imported : [...nextApplications, ...imported]);
      return { imported: imported.length, merged, skipped, mode };
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
    const tasksMatch = request.pathname.match(/^\/api\/applications\/(\d+)\/tasks$/);
    if (tasksMatch && method === "GET") {
      const application = applications.find((item) => item.id === Number(tasksMatch[1]));
      if (!application) throw makeError("Application not found.");
      return copy((application.tasks || []).sort((left, right) => (left.completed_at ? 1 : 0) - (right.completed_at ? 1 : 0) || left.due_date.localeCompare(right.due_date) || left.id - right.id));
    }
    if (tasksMatch && method === "POST") {
      const application = applications.find((item) => item.id === Number(tasksMatch[1]));
      if (!application) throw makeError("Application not found.");
      if (application.stage === "Closed") throw makeApiError("Closed applications cannot receive new tasks.", 422, { application_id: "Closed applications cannot receive new tasks." }, { code: "INVALID_TASK" });
      const task = validateTask(parseBody(options));
      const next = copy(applications);
      const target = next.find((item) => item.id === application.id);
      const nextTaskId = next.flatMap((item) => item.tasks || []).reduce((maximum, item) => Math.max(maximum, item.id || 0), 0) + 1;
      target.tasks.push({ id: nextTaskId, application_id: target.id, ...task, created_at: nowIso(), updated_at: nowIso() });
      syncNextActionDate(target);
      commit(next);
      return copy(target.tasks[target.tasks.length - 1]);
    }
    const taskCompleteMatch = request.pathname.match(/^\/api\/tasks\/(\d+)\/complete$/);
    const taskSnoozeMatch = request.pathname.match(/^\/api\/tasks\/(\d+)\/snooze$/);
    const taskMatch = request.pathname.match(/^\/api\/tasks\/(\d+)$/);
    const requirementMatch = request.pathname.match(/^\/api\/requirements\/(\d+)$/);
    const artifactMatch = request.pathname.match(/^\/api\/artifacts\/(\d+)$/);
    if (artifactMatch && method === "PATCH") {
      const artifactId = Number(artifactMatch[1]);
      const currentApplication = applications.find((item) => (item.artifacts || []).some((artifact) => artifact.id === artifactId));
      if (!currentApplication) throw makeError("Artifact not found.");
      const payload = validateArtifact(parseBody(options), { partial: true });
      const next = copy(applications);
      const targetApplication = next.find((item) => item.id === currentApplication.id);
      const artifact = targetApplication.artifacts.find((item) => item.id === artifactId);
      Object.keys(payload).forEach((field) => { artifact[field] = payload[field]; });
      artifact.updated_at = nowIso();
      commit(next);
      return copy(artifact);
    }
    if (artifactMatch && method === "DELETE") {
      const artifactId = Number(artifactMatch[1]);
      const currentApplication = applications.find((item) => (item.artifacts || []).some((artifact) => artifact.id === artifactId));
      if (!currentApplication) throw makeError("Artifact not found.");
      if (applications.some((item) => (item.submissions || []).some((submission) => (submission.items || []).some((entry) => entry.artifact_id === artifactId)))) {
        throw makeApiError("This material is referenced by an immutable submission package.", 409, {}, { code: "ARTIFACT_IN_USE" });
      }
      const next = copy(applications);
      const targetApplication = next.find((item) => item.id === currentApplication.id);
      targetApplication.artifacts = (targetApplication.artifacts || []).filter((artifact) => artifact.id !== artifactId);
      commit(next);
      return null;
    }
    if (requirementMatch && method === "PATCH") {
      const requirementId = Number(requirementMatch[1]);
      const currentApplication = applications.find((item) => (item.requirements || []).some((requirement) => requirement.id === requirementId));
      if (!currentApplication) throw makeError("Requirement not found.");
      const payload = validateRequirement(parseBody(options), { partial: true });
      const next = copy(applications);
      const targetApplication = next.find((item) => item.id === currentApplication.id);
      const requirement = targetApplication.requirements.find((item) => item.id === requirementId);
      Object.keys(payload).forEach((field) => { requirement[field] = payload[field]; });
      requirement.updated_at = nowIso();
      commit(next);
      return copy(requirement);
    }
    if (requirementMatch && method === "DELETE") {
      const requirementId = Number(requirementMatch[1]);
      const currentApplication = applications.find((item) => (item.requirements || []).some((requirement) => requirement.id === requirementId));
      if (!currentApplication) throw makeError("Requirement not found.");
      const next = copy(applications);
      const targetApplication = next.find((item) => item.id === currentApplication.id);
      targetApplication.requirements = (targetApplication.requirements || []).filter((requirement) => requirement.id !== requirementId);
      commit(next);
      return null;
    }
    if (taskCompleteMatch && method === "POST") {
      const taskId = Number(taskCompleteMatch[1]);
      const currentApplication = applications.find((item) => (item.tasks || []).some((task) => task.id === taskId));
      if (!currentApplication) throw makeError("Task not found.");
      const payload = parseBody(options);
      if (payload.expected_version != null && (!Number.isInteger(payload.expected_version) || payload.expected_version < 1)) throw makeApiError("Validation failed.", 422, { expected_version: "Version must be a positive integer." });
      const next = copy(applications);
      const targetApplication = next.find((item) => item.id === currentApplication.id);
      const task = targetApplication.tasks.find((item) => item.id === taskId);
      if (task.completed_at) return copy(task);
      if (payload.expected_version != null && task.version !== payload.expected_version) throw makeApiError("The task was changed by another request.", 409, {}, { code: "VERSION_CONFLICT", current: copy(task) });
      task.completed_at = nowIso(); task.version += 1; task.updated_at = nowIso();
      syncNextActionDate(targetApplication); commit(next); return copy(task);
    }
    if (taskSnoozeMatch && method === "POST") {
      const taskId = Number(taskSnoozeMatch[1]);
      const currentApplication = applications.find((item) => (item.tasks || []).some((task) => task.id === taskId));
      if (!currentApplication) throw makeError("Task not found.");
      const payload = validateTask(parseBody(options), { partial: true });
      if (!payload.due_date || payload.expected_version == null) throw makeApiError("Validation failed.", 422, { body: "Snooze requires due_date and expected_version." });
      const next = copy(applications); const targetApplication = next.find((item) => item.id === currentApplication.id); const task = targetApplication.tasks.find((item) => item.id === taskId);
      if (task.completed_at) throw makeApiError("Completed tasks cannot be snoozed.", 422, { task_id: "Completed tasks cannot be snoozed." }, { code: "INVALID_TASK" });
      if (task.version !== payload.expected_version) throw makeApiError("The task was changed by another request.", 409, {}, { code: "VERSION_CONFLICT", current: copy(task) });
      task.due_date = payload.due_date; task.version += 1; task.updated_at = nowIso(); syncNextActionDate(targetApplication); commit(next); return copy(task);
    }
    if (taskMatch && method === "PATCH") {
      const taskId = Number(taskMatch[1]);
      const currentApplication = applications.find((item) => (item.tasks || []).some((task) => task.id === taskId));
      if (!currentApplication) throw makeError("Task not found.");
      const payload = validateTask(parseBody(options), { partial: true });
      const next = copy(applications); const targetApplication = next.find((item) => item.id === currentApplication.id); const task = targetApplication.tasks.find((item) => item.id === taskId);
      if (payload.expected_version != null && task.version !== payload.expected_version) throw makeApiError("The task was changed by another request.", 409, {}, { code: "VERSION_CONFLICT", current: copy(task) });
      ["kind", "title", "due_date", "completed_at"].forEach((field) => { if (Object.prototype.hasOwnProperty.call(payload, field)) task[field] = payload[field]; });
      task.version += 1; task.updated_at = nowIso(); syncNextActionDate(targetApplication); commit(next); return copy(task);
    }
    const transitionMatch = request.pathname.match(/^\/api\/applications\/(\d+)\/transitions$/);
    if (transitionMatch && method === "POST") {
      const id = Number(transitionMatch[1]);
      const application = applications.find((item) => item.id === id);
      if (!application) throw makeError("Application not found.");
      const transition = validateTransition(parseBody(options));
      if (transition.request_id) {
        const previous = applications.flatMap((item) => item.events || []).find((event) => event.request_id === transition.request_id);
        if (previous) {
          if (previous.application_id !== id) throw makeApiError("Request ID was already used for another application.", 409, {}, { code: "REQUEST_ID_CONFLICT" });
          return { application: copy(application), event: copy(previous), replayed: true };
        }
      }
      if (transition.expected_version != null && application.version !== transition.expected_version) {
        throw makeApiError("The application was changed by another request.", 409, {}, { code: "VERSION_CONFLICT", current: copy(application) });
      }
      const fromStage = application.stage;
      if (!allowedTransitions[fromStage]?.has(transition.to_stage)) throw makeApiError(`Cannot move an application from ${fromStage} to ${transition.to_stage}.`, 422, { to_stage: "Invalid transition." }, { code: "INVALID_TRANSITION" });
      const next = copy(applications);
      const target = next.find((item) => item.id === id);
      target.stage = transition.to_stage;
      target.status = stageToLegacy[transition.to_stage];
      target.outcome = transition.to_stage === "Closed" ? transition.outcome : null;
      target.closed_at = transition.to_stage === "Closed" ? transition.occurred_at : null;
      target.version = (target.version || 1) + 1;
      target.updated_at = nowIso();
      if (transition.to_stage === "Closed") {
        target.tasks = (target.tasks || []).map((task) => task.completed_at ? task : { ...task, completed_at: transition.occurred_at, version: (task.version || 1) + 1, updated_at: nowIso() });
        target.waiting_until = null;
        syncNextActionDate(target);
      }
      addEvent(target, {
        event_type: "status_changed",
        title: `Stage changed to ${transition.to_stage}`,
        details: `Previous stage: ${fromStage}`,
        occurred_at: transition.occurred_at,
        from_stage: fromStage,
        to_stage: transition.to_stage,
        origin: "system",
        payload_json: { from_stage: fromStage, to_stage: transition.to_stage, outcome: transition.outcome, expected_version: transition.expected_version },
        request_id: transition.request_id,
      });
      commit(next);
      const event = target.events[target.events.length - 1];
      return { application: copy(target), event: copy(event), replayed: false };
    }
    const eventMatch = request.pathname.match(/^\/api\/applications\/(\d+)\/events\/(\d+)$/);
    if (eventMatch && method === "DELETE") {
      const application = applications.find((item) => item.id === Number(eventMatch[1]));
      if (!application) throw makeError("Application not found.");
      const eventId = Number(eventMatch[2]);
      const next = copy(applications);
      const target = next.find((item) => item.id === application.id);
      const events = target.events || [];
      const event = events.find((item) => item.id === eventId);
      if (!event) throw makeError("Event not found.");
      if (event.origin !== "user") throw makeApiError(`Events with origin '${event.origin || "legacy"}' cannot be deleted.`, 403, {}, { code: "PROTECTED_EVENT" });
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
      const payload = parseBody(options);
      const cleaned = validateApplication(payload, applications[index]);
      if (cleaned.stage !== applications[index].stage) {
        const transitionPayload = {
          to_stage: cleaned.stage,
          outcome: cleaned.outcome,
          occurred_at: cleaned.closed_at,
          expected_version: applications[index].version,
        };
        const transition = validateTransition(transitionPayload);
        const next = copy(applications);
        const target = next[index];
        target.stage = transition.to_stage;
        target.status = stageToLegacy[transition.to_stage];
        target.outcome = transition.outcome;
        target.closed_at = transition.to_stage === "Closed" ? transition.occurred_at : null;
        target.version = (target.version || 1) + 1;
        target.updated_at = nowIso();
        if (transition.to_stage === "Closed") {
          target.tasks = (target.tasks || []).map((task) => task.completed_at ? task : { ...task, completed_at: transition.occurred_at, version: (task.version || 1) + 1, updated_at: nowIso() });
          target.waiting_until = null;
          syncNextActionDate(target);
        }
        addEvent(target, { event_type: "status_changed", title: `Stage changed to ${transition.to_stage}`, details: `Previous stage: ${applications[index].stage}`, occurred_at: transition.occurred_at, from_stage: applications[index].stage, to_stage: transition.to_stage, origin: "system", payload_json: { from_stage: applications[index].stage, to_stage: transition.to_stage }, request_id: null });
        const ordinary = ["company", "role", "location", "work_mode", "source", "url", "salary_min", "salary_max", "salary_period", "currency", "applied_date", "next_action_date", "waiting_until", "notes"];
        ordinary.forEach((field) => { if (transition.to_stage !== "Closed" || !["next_action_date", "waiting_until"].includes(field)) target[field] = cleaned[field]; });
        if (transition.to_stage !== "Closed" && cleaned.next_action_date !== applications[index].next_action_date) {
          reconcileCompatibilityTask(target, cleaned.next_action_date);
          addEvent(target, { event_type: "follow_up", title: "Follow-up date updated", details: `Next action: ${cleaned.next_action_date || "No date"}`, origin: "user" });
        }
        if (cleaned.notes !== applications[index].notes) addEvent(target, { event_type: "note", title: "Notes updated", details: cleaned.notes || "Notes cleared.", origin: "user" });
        commit(next);
        return copy(target);
      }
      const updated = { ...applications[index], ...cleaned, version: (applications[index].version || 1) + 1, updated_at: nowIso() };
      if (cleaned.status !== applications[index].status) {
        addEvent(updated, { event_type: "status_changed", title: `Status changed to ${cleaned.status}`, details: `Previous status: ${applications[index].status}`, occurred_at: updated.updated_at, origin: "system" });
      }
      if (cleaned.next_action_date !== applications[index].next_action_date) {
        reconcileCompatibilityTask(updated, cleaned.next_action_date);
        addEvent(updated, { event_type: "follow_up", title: "Follow-up date updated", details: `Next action: ${cleaned.next_action_date || "No date"}`, occurred_at: updated.updated_at, origin: "user" });
      }
      if (cleaned.notes !== applications[index].notes) {
        addEvent(updated, { event_type: "note", title: "Notes updated", details: cleaned.notes || "Notes cleared.", occurred_at: updated.updated_at, origin: "user" });
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
