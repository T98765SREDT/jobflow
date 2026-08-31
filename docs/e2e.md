# Browser workflow evidence

JobFlow's main application is a small Python server plus a vanilla JavaScript
browser client. The Playwright check in
[`tests/e2e/jobflow.spec.mjs`](../tests/e2e/jobflow.spec.mjs) exercises the
boundary between those two pieces instead of mocking the API.

## What is covered

- an empty workspace can add an application and open its workspace;
- a record can move through Ready, Applied, Interview, and Closed;
- requirements, evidence, tasks, completion, materials, and an immutable
  submission snapshot are persisted and visible after the page re-renders;
- the activity timeline and Insights view remain reachable after a close;
- a failed write leaves the form draft available for recovery;
- `Control+K` focuses search and the main page opens at a 375px viewport.

The CI workflow runs the same spec separately in Chromium, Firefox, and WebKit.
The test data uses unique names and a temporary database, so it never touches a
developer's local `data/jobflow.db`.

## Run locally

From the repository root, install the test-only dependency and one browser:

```bash
npm install
npx playwright install chromium
python3 app.py --db /tmp/jobflow-e2e.db --port 8765
```

In another terminal:

```bash
JOBFLOW_BROWSER=chromium npm run test:browser
```

Set `JOBFLOW_E2E_URL` if the server is running somewhere else. Firefox and
WebKit use the same command with `JOBFLOW_BROWSER=firefox` or
`JOBFLOW_BROWSER=webkit`.

These checks are evidence about the tested workflow, not a claim that every
possible browser interaction is covered. Product bugs found by a check should
be fixed in a separate focused change rather than hidden in the test.
