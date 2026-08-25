"use strict";

// GitHub Pages can host the interface but not the Python API. This adapter keeps
// the public preview interactive while the production code path remains the API
// implementation in app.js. It is intentionally enabled only on GitHub Pages or
// when ?demo=1 is supplied for local portfolio review.
const previewParameters = new URLSearchParams(window.location.search);
const isPortfolioPreview = window.location.hostname.endsWith("github.io") || previewParameters.has("demo");

if (isPortfolioPreview) {
  const previewOptions = {
    statuses: ["Wishlist", "Applied", "Interview", "Offer", "Rejected"],
    work_modes: ["Remote", "Hybrid", "On-site"],
    currencies: ["USD", "JPY", "EUR", "GBP"],
  };
  let nextId = 7;
  let applications = [
    { id: 1, company: "Northstar Labs", role: "Python Backend Developer", location: "Worldwide", work_mode: "Remote", status: "Interview", source: "LinkedIn", url: "https://example.com/jobs/northstar", salary_min: 32, salary_max: 48, currency: "USD", applied_date: "2026-08-17", next_action_date: "2026-08-26", notes: "Prepare API design examples and questions for the engineering team.", created_at: "2026-08-17T09:00:00Z", updated_at: "2026-08-24T09:00:00Z" },
    { id: 2, company: "Lumen AI", role: "AI Code Evaluator — Mandarin", location: "Japan", work_mode: "Remote", status: "Applied", source: "Company site", url: "https://example.com/jobs/lumen", salary_min: 28, salary_max: 40, currency: "USD", applied_date: "2026-08-22", next_action_date: "2026-08-29", notes: "Submitted coding assessment. Follow up if there is no response.", created_at: "2026-08-22T09:00:00Z", updated_at: "2026-08-24T10:00:00Z" },
    { id: 3, company: "Sora Systems", role: "Junior Full-Stack Engineer", location: "Tokyo, Japan", work_mode: "Hybrid", status: "Wishlist", source: "Referral", url: "https://example.com/jobs/sora", salary_min: 4200000, salary_max: 5500000, currency: "JPY", applied_date: null, next_action_date: "2026-08-27", notes: "Tailor portfolio summary to the product dashboard requirements.", created_at: "2026-08-23T09:00:00Z", updated_at: "2026-08-23T09:00:00Z" },
    { id: 4, company: "Orbit QA", role: "Freelance Software Tester", location: "Worldwide", work_mode: "Remote", status: "Offer", source: "Remote board", url: "https://example.com/jobs/orbit", salary_min: 22, salary_max: 28, currency: "USD", applied_date: "2026-08-11", next_action_date: "2026-08-27", notes: "Review contractor agreement and weekly availability.", created_at: "2026-08-11T09:00:00Z", updated_at: "2026-08-25T09:00:00Z" },
    { id: 5, company: "Maple Cloud", role: "Web Developer", location: "Singapore", work_mode: "Remote", status: "Rejected", source: "LinkedIn", url: "https://example.com/jobs/maple", salary_min: 3000, salary_max: 4500, currency: "USD", applied_date: "2026-07-31", next_action_date: null, notes: "Good practice interview; strengthen system-design examples.", created_at: "2026-07-31T09:00:00Z", updated_at: "2026-08-20T09:00:00Z" },
    { id: 6, company: "Kite Data", role: "Technical Data Analyst", location: "Japan", work_mode: "Remote", status: "Applied", source: "Company site", url: "https://example.com/jobs/kite", salary_min: 250000, salary_max: 350000, currency: "JPY", applied_date: "2026-08-24", next_action_date: "2026-08-31", notes: "Highlight SQL validation and structured-data experience.", created_at: "2026-08-24T09:00:00Z", updated_at: "2026-08-24T09:00:00Z" },
  ];

  const copy = (value) => JSON.parse(JSON.stringify(value));
  const parseBody = (options) => options.body ? JSON.parse(options.body) : {};
  const asDate = (value) => new Date(`${value}T00:00:00`).valueOf();

  function list(query) {
    const search = (query.get("search") || "").toLowerCase();
    const status = query.get("status") || "";
    const workMode = query.get("work_mode") || "";
    const sort = query.get("sort") || "updated_at";
    const direction = (query.get("direction") || "desc").toLowerCase() === "asc" ? 1 : -1;
    return applications.filter((application) => {
      const haystack = [application.company, application.role, application.location, application.notes].join(" ").toLowerCase();
      return (!search || haystack.includes(search)) && (!status || application.status === status) && (!workMode || application.work_mode === workMode);
    }).sort((left, right) => {
      const dateSort = ["applied_date", "next_action_date"].includes(sort);
      if (dateSort && (!left[sort] || !right[sort])) {
        if (!left[sort] && !right[sort]) return right.id - left.id;
        return left[sort] ? -1 : 1;
      }
      const leftValue = dateSort ? asDate(left[sort]) : String(left[sort] || "").toLowerCase();
      const rightValue = dateSort ? asDate(right[sort]) : String(right[sort] || "").toLowerCase();
      return (leftValue > rightValue ? 1 : leftValue < rightValue ? -1 : right.id - left.id) * direction;
    });
  }

  function analytics() {
    const byStatus = Object.fromEntries(previewOptions.statuses.map((status) => [status, 0]));
    applications.forEach((application) => { byStatus[application.status] += 1; });
    const total = applications.length;
    const interviews = applications.filter((application) => ["Interview", "Offer"].includes(application.status)).length;
    return {
      total,
      active: applications.filter((application) => ["Applied", "Interview", "Offer"].includes(application.status)).length,
      interviews,
      response_rate: total ? Math.round((interviews / total) * 100) : 0,
      by_status: byStatus,
      upcoming: applications.filter((application) => application.next_action_date && application.status !== "Rejected").sort((left, right) => asDate(left.next_action_date) - asDate(right.next_action_date)).slice(0, 5),
    };
  }

  window.JobFlowDemoApi = async (path, options = {}) => {
    const request = new URL(path, "https://jobflow.preview");
    const method = (options.method || "GET").toUpperCase();
    if (request.pathname === "/api/meta/options" && method === "GET") return copy(previewOptions);
    if (request.pathname === "/api/analytics" && method === "GET") return copy(analytics());
    if (request.pathname === "/api/applications" && method === "GET") return { items: copy(list(request.searchParams)) };
    if (request.pathname === "/api/applications" && method === "POST") {
      const application = { id: nextId++, ...parseBody(options), created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      applications = [application, ...applications];
      return copy(application);
    }
    const match = request.pathname.match(/^\/api\/applications\/(\d+)$/);
    if (match && method === "PATCH") {
      const id = Number(match[1]);
      const index = applications.findIndex((application) => application.id === id);
      if (index < 0) throw new Error("Application not found.");
      applications[index] = { ...applications[index], ...parseBody(options), updated_at: new Date().toISOString() };
      return copy(applications[index]);
    }
    if (match && method === "DELETE") {
      applications = applications.filter((application) => application.id !== Number(match[1]));
      return null;
    }
    throw new Error("This preview route is unavailable.");
  };
}
