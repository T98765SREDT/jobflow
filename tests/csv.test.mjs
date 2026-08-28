import test from "node:test";
import assert from "node:assert/strict";
import "../static/csv.js";

const csv = globalThis.JobFlowCsv;

test("parses quoted commas, escaped quotes, and newlines", () => {
  const parsed = csv.parse('Company,Role,Notes\n"North, Inc.",Developer,"Line one\nLine ""two"""\n');
  assert.deepEqual(parsed.headers, ["Company", "Role", "Notes"]);
  assert.equal(parsed.rows[0].values[0], "North, Inc.");
  assert.equal(parsed.rows[0].values[2], 'Line one\nLine "two"');
});

test("infers common headers and skips duplicate records", () => {
  const parsed = csv.parse("Employer,Position,Link,Status,Work mode,Minimum salary\nAcme,Engineer,https://acme.test,applied,on site," + '"$30,000"' + "\nAcme,Engineer,https://acme.test,applied,on site," + '"$30,000"' + "\n");
  const mapping = csv.inferMapping(parsed.headers);
  const result = csv.toBackup(parsed, mapping);
  assert.equal(mapping.company, "0");
  assert.equal(mapping.role, "1");
  assert.equal(result.records.length, 1);
  assert.equal(result.duplicates, 1);
  assert.equal(result.records[0].status, "Applied");
  assert.equal(result.records[0].work_mode, "On-site");
  assert.equal(result.records[0].salary_min, 30000);
});

test("reports rows missing required fields before import", () => {
  const parsed = csv.parse("Company,Role\n,Engineer\nAcme,\n");
  const result = csv.toBackup(parsed, csv.inferMapping(parsed.headers));
  assert.equal(result.records.length, 0);
  assert.equal(result.errors.length, 2);
});
