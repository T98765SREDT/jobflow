import test from "node:test";
import assert from "node:assert/strict";
import "../static/csv.js";

const csv = globalThis.JobFlowCsv;

test("canonical URL vectors match the import identity rules", () => {
  assert.equal(csv.canonicalUrl("HTTPS://Example.COM:443/jobs//42/?utm_source=mail&job=42#description"), "https://example.com/jobs/42?job=42");
  assert.equal(csv.canonicalUrl("https://user:secret@example.com/jobs/42"), "https://example.com/jobs/42");
});

test("fallback fingerprint handles Unicode and distinguishes location", () => {
  const left = { company: "Ａｃｍｅ  Labs", role: " Python  Developer ", location: "Tokyo" };
  const right = { company: "Acme Labs", role: "Python Developer", location: " Tokyo " };
  assert.equal(csv.applicationFingerprint(left), csv.applicationFingerprint(right));
  assert.equal(csv.duplicateReason(left, right), "company_role_location");
  assert.notEqual(csv.applicationFingerprint({ ...right, location: "Osaka" }), csv.applicationFingerprint(left));
});

test("identity-bearing query parameters remain distinct", () => {
  const first = { url: "https://example.com/job?id=7&utm_campaign=a" };
  const second = { url: "https://example.com/job?id=8&utm_campaign=b" };
  assert.notEqual(csv.applicationFingerprint(first), csv.applicationFingerprint(second));
});
