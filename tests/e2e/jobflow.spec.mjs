import { test, expect } from "@playwright/test";

const baseURL = process.env.JOBFLOW_E2E_URL || "http://127.0.0.1:8765";
const browserName = process.env.JOBFLOW_BROWSER || "chromium";
const unique = (label) => `${label} ${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

test.use({ baseURL, browserName });
test.describe.configure({ mode: "serial" });

async function openWorkspace(page) {
  await page.goto("/");
  await expect(page.locator("#applications-body")).toBeVisible();
  await expect(page.locator("#loading-state")).toBeHidden();
}

async function addApplication(page, company, role) {
  await page.getByRole("button", { name: "Add application" }).click();
  const form = page.locator("#application-form");
  await expect(form).toBeVisible();
  await expect(form.locator('select[name="status"] option')).not.toHaveCount(0);
  await form.locator('input[name="company"]').fill(company);
  await form.locator('input[name="role"]').fill(role);
  await form.locator('select[name="status"]').selectOption("Wishlist");
  await form.locator('input[name="next_action_date"]').fill("2099-01-15");
  await form.locator("#more-details").evaluate((details) => { details.open = true; });
  await form.locator('select[name="work_mode"] option').first().waitFor();
  await form.locator('select[name="work_mode"]').selectOption("Remote");
  await form.locator('input[name="location"]').fill("Worldwide");
  await form.locator('input[name="source"]').fill("E2E browser check");
  await form.locator('textarea[name="notes"]').fill("Synthetic record used by the browser workflow test.");
  await form.locator('button[type="submit"]').click();
  await expect(form).toBeHidden();
  const row = page.locator("#applications-body tr").filter({ hasText: company });
  await expect(row).toBeVisible();
  return row;
}

async function moveTo(page, stage) {
  const stageSelect = page.locator("#transition-stage");
  await stageSelect.selectOption(stage);
  if (stage === "Closed") {
    await expect(page.locator("#transition-outcome-label")).toBeVisible();
    await page.locator("#transition-outcome").selectOption("Rejected");
  }
  await page.getByRole("button", { name: "Save stage" }).click();
  await expect(page.locator("#transition-stage")).toHaveValue(stage);
}

test("primary workflow records requirements, tasks, materials and history", async ({ page }) => {
  await openWorkspace(page);
  const company = unique("Browser Proof Co");
  const role = "Python QA Engineer";
  const row = await addApplication(page, company, role);
  await row.getByRole("button", { name: new RegExp(`View ${role}`) }).click();
  await expect(page.locator("#details-dialog")).toBeVisible();

  await moveTo(page, "Ready");
  await moveTo(page, "Applied");

  await page.locator('[data-workspace-tab="requirements"]').click();
  const requirementForm = page.locator("#requirement-form");
  await requirementForm.locator('input[name="criterion"]').fill("Python API testing");
  await requirementForm.locator('select[name="assessment"]').selectOption("met");
  await requirementForm.locator('textarea[name="evidence"]').fill("QA Sentinel project and automated API checks.");
  await requirementForm.locator('button[type="submit"]').click();
  await expect(page.locator(".requirement-row")).toContainText("Python API testing");

  await page.locator('[data-workspace-tab="tasks"]').click();
  const taskForm = page.locator("#task-form");
  await taskForm.locator('input[name="title"]').fill("Prepare API test examples");
  await taskForm.locator('input[name="due_date"]').fill("2099-01-16");
  await taskForm.locator('button[type="submit"]').click();
  await expect(page.locator(".task-row")).toContainText("Prepare API test examples");
  await page.locator(".task-row").getByRole("button", { name: "Complete" }).click();
  await expect(page.locator(".task-completed")).toContainText("Prepare API test examples");

  await moveTo(page, "Interview");

  await page.locator('[data-workspace-tab="materials"]').click();
  const materialForm = page.locator("#artifact-form");
  await materialForm.locator('input[name="version_label"]').fill("v1 · browser proof");
  await materialForm.locator('input[name="label"]').fill("QA portfolio resume");
  await materialForm.locator('textarea[name="notes"]').fill("Version used for the browser workflow check.");
  await materialForm.locator('button[type="submit"]').click();
  await expect(page.locator(".material-row")).toContainText("QA portfolio resume");
  await page.locator('#submission-form input[name="artifact_id"]').first().check();
  await page.locator("#submission-form").getByRole("button", { name: "Create submission snapshot" }).click();
  await expect(page.locator(".snapshot-badge")).toContainText("Read-only snapshot");

  await page.locator('[data-workspace-tab="overview"]').click();
  await moveTo(page, "Closed");
  await page.locator('[data-workspace-tab="activity"]').click();
  await expect(page.locator("#workspace-panel-activity")).toContainText("Stage change");
  await page.locator("#close-details").click();

  await page.getByRole("link", { name: "Insights" }).click();
  await expect(page.locator("#historical-insights-panel")).toBeVisible();
});

test("drafts survive a failed write and the keyboard shortcut focuses search", async ({ page }) => {
  await openWorkspace(page);
  await page.keyboard.press("Control+K");
  await expect(page.locator("#search")).toBeFocused();

  await page.getByRole("button", { name: "Add application" }).click();
  const form = page.locator("#application-form");
  const company = unique("Offline Draft Co");
  await form.locator('input[name="company"]').fill(company);
  await form.locator('input[name="role"]').fill("Recovery Test Engineer");
  await form.locator('select[name="status"]').selectOption("Wishlist");
  await page.route("**/api/applications", async (route) => {
    if (route.request().method() === "POST") await route.abort();
    else await route.continue();
  });
  await form.locator('button[type="submit"]').click();
  await expect(page.locator("#form-errors")).toBeVisible();
  await page.unroute("**/api/applications");
  await page.locator("#close-dialog").click();
  await page.getByRole("button", { name: "Add application" }).click();
  await expect(form.locator('input[name="company"]')).toHaveValue(company);
  await expect(form.locator('input[name="role"]')).toHaveValue("Recovery Test Engineer");
});

test("stale transitions and invalid replace imports preserve the current record", async ({ page }) => {
  await openWorkspace(page);
  const company = unique("Recovery Proof Co");
  const create = await page.request.post("/api/applications", {
    data: { company, role: "Concurrency Test Engineer", status: "Wishlist", work_mode: "Remote" },
  });
  expect(create.ok()).toBeTruthy();
  const created = await create.json();
  const staleVersion = created.version;
  const transition = await page.request.post(`/api/applications/${created.id}/transitions`, {
    data: { to_stage: "Ready", expected_version: staleVersion, request_id: unique("e2e-transition") },
  });
  expect(transition.ok()).toBeTruthy();

  const stale = await page.request.post(`/api/applications/${created.id}/transitions`, {
    data: { to_stage: "Applied", expected_version: staleVersion, request_id: unique("e2e-stale") },
  });
  expect(stale.status()).toBe(409);
  expect(stale.headers()["x-request-id"]).toBeTruthy();
  expect((await stale.json()).error.code).toBe("VERSION_CONFLICT");

  const before = await (await page.request.get("/api/export")).json();
  const duplicatePreview = await page.request.post("/api/import/preview", {
    data: { applications: [{ company, role: "Concurrency Test Engineer", status: "Wishlist", work_mode: "Remote" }] },
  });
  expect(duplicatePreview.ok()).toBeTruthy();
  expect((await duplicatePreview.json()).conflicts.length).toBeGreaterThan(0);
  const duplicateImport = await page.request.post("/api/import?mode=append", {
    data: { applications: [{ company, role: "Concurrency Test Engineer", status: "Wishlist", work_mode: "Remote" }] },
  });
  expect(duplicateImport.status()).toBe(409);
  const invalidRestore = await page.request.post("/api/import?mode=replace", {
    data: { schema_version: 99, applications: [] },
  });
  expect(invalidRestore.status()).toBe(422);
  const after = await (await page.request.get("/api/export")).json();
  expect(after.applications).toEqual(before.applications);
  expect(after.applications.some((application) => application.id === created.id && application.stage === "Ready")).toBeTruthy();
});

test("the dashboard remains usable at a 375px viewport", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openWorkspace(page);
  await expect(page.locator("body")).toBeVisible();
  await expect(page.locator("#applications")).toBeVisible();
  await page.getByRole("button", { name: "Add application" }).click();
  await expect(page.locator("#application-dialog")).toBeVisible();
  await page.locator("#close-dialog").click();
});
