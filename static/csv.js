"use strict";

// Small, dependency-free CSV helpers used by the browser demo and the local app UI.
// The parser intentionally supports quoted commas, newlines, and escaped quotes so
// exports from spreadsheet tools can be previewed before they touch the workspace.
(function exposeJobFlowCsv() {
  const root = typeof window !== "undefined" ? window : globalThis;
  const fields = [
    { key: "company", label: "Company", aliases: ["company", "employer", "organization", "公司", "企业"] },
    { key: "role", label: "Role", aliases: ["role", "title", "position", "job title", "职位", "岗位"] },
    { key: "location", label: "Location", aliases: ["location", "city", "地点", "位置"] },
    { key: "work_mode", label: "Work mode", aliases: ["work mode", "work_mode", "remote", "工作方式"] },
    { key: "status", label: "Status", aliases: ["status", "stage", "pipeline stage", "状态", "阶段"] },
    { key: "source", label: "Source", aliases: ["source", "channel", "来源", "渠道"] },
    { key: "url", label: "Job URL", aliases: ["url", "job url", "link", "链接", "网址"] },
    { key: "salary_min", label: "Minimum salary", aliases: ["salary min", "salary_min", "min salary", "minimum salary", "最低薪资"] },
    { key: "salary_max", label: "Maximum salary", aliases: ["salary max", "salary_max", "max salary", "maximum salary", "最高薪资"] },
    { key: "salary_period", label: "Salary period", aliases: ["salary period", "salary_period", "period", "薪资周期"] },
    { key: "currency", label: "Currency", aliases: ["currency", "currency code", "货币"] },
    { key: "applied_date", label: "Applied date", aliases: ["applied date", "applied_date", "date applied", "申请日期"] },
    { key: "next_action_date", label: "Next action", aliases: ["next action", "next_action_date", "follow up", "follow-up", "下次行动", "跟进日期"] },
    { key: "notes", label: "Notes", aliases: ["notes", "note", "comments", "备注"] },
  ];

  const normalizeHeader = (value) => String(value ?? "")
    .replace(/^\ufeff/, "")
    .trim()
    .toLowerCase()
    .replace(/[：:]/g, " ")
    .replace(/[_\-]+/g, " ")
    .replace(/\s+/g, " ");

  function parse(text) {
    const input = String(text ?? "").replace(/^\ufeff/, "");
    const rows = [];
    let row = [];
    let cell = "";
    let quoted = false;
    for (let index = 0; index < input.length; index += 1) {
      const character = input[index];
      if (quoted) {
        if (character === '"' && input[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else if (character === '"') {
          quoted = false;
        } else {
          cell += character;
        }
      } else if (character === '"' && cell.length === 0) {
        quoted = true;
      } else if (character === ",") {
        row.push(cell);
        cell = "";
      } else if (character === "\n" || character === "\r") {
        if (character === "\r" && input[index + 1] === "\n") index += 1;
        row.push(cell);
        if (row.some((value) => value.trim())) rows.push(row);
        row = [];
        cell = "";
      } else {
        cell += character;
      }
    }
    if (quoted) throw new Error("The CSV contains an unfinished quoted field.");
    if (cell.length || row.length) {
      row.push(cell);
      if (row.some((value) => value.trim())) rows.push(row);
    }
    if (rows.length < 2) throw new Error("The CSV needs a header row and at least one data row.");
    const headers = rows[0].map((value, index) => String(value).trim() || `Column ${index + 1}`);
    const data = rows.slice(1).map((values, rowIndex) => ({
      rowNumber: rowIndex + 2,
      values: headers.map((_header, index) => String(values[index] ?? "").trim()),
    }));
    return { headers, rows: data };
  }

  function inferMapping(headers) {
    const normalized = headers.map(normalizeHeader);
    return Object.fromEntries(fields.map((field) => {
      const aliases = field.aliases.map(normalizeHeader);
      const index = normalized.findIndex((header) => aliases.includes(header));
      return [field.key, index >= 0 ? String(index) : ""];
    }));
  }

  function valueFor(row, mapping, key) {
    const index = mapping[key];
    return index === "" || index == null ? "" : String(row.values[Number(index)] ?? "").trim();
  }

  function normalizeChoice(value, choices, aliases = {}) {
    const normalized = String(value ?? "").trim().toLowerCase();
    if (!normalized) return "";
    const alias = aliases[normalized] || normalized;
    return choices.find((choice) => choice.toLowerCase() === alias) || String(value).trim();
  }

  function normalizeAmount(value) {
    const cleaned = String(value ?? "").trim();
    if (!cleaned) return null;
    const number = Number(cleaned.replace(/[,$€¥£\s]/g, ""));
    return Number.isFinite(number) ? number : cleaned;
  }

  const trackingQueryParameters = new Set([
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
  ]);

  function canonicalUrl(value) {
    const text = String(value ?? "").trim();
    if (!text) return "";
    try {
      const parsed = new URL(text);
      if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) return "";
      parsed.hash = "";
      parsed.username = "";
      parsed.password = "";
      parsed.pathname = (parsed.pathname || "/").replace(/\/{2,}/g, "/");
      if (parsed.pathname !== "/") parsed.pathname = parsed.pathname.replace(/\/+$/, "");
      const kept = [...parsed.searchParams]
        .filter(([key]) => !trackingQueryParameters.has(key.toLowerCase()))
        .sort(([leftKey, leftValue], [rightKey, rightValue]) => {
          const left = `${leftKey}\u0000${leftValue}`;
          const right = `${rightKey}\u0000${rightValue}`;
          return left < right ? -1 : left > right ? 1 : 0;
        });
      parsed.search = new URLSearchParams(kept).toString();
      return parsed.toString();
    } catch (_error) {
      return "";
    }
  }

  function normalizeIdentityText(value) {
    return String(value ?? "").normalize("NFKC").trim().toLocaleLowerCase().split(/\s+/u).filter(Boolean).join(" ");
  }

  function applicationFingerprint(record) {
    const url = canonicalUrl(record?.url);
    return url
      ? `url:${url}`
      : `details:${["company", "role", "location"].map((field) => normalizeIdentityText(record?.[field])).join("\u001f")}`;
  }

  function duplicateReason(incoming, existing) {
    const incomingUrl = canonicalUrl(incoming?.url);
    const existingUrl = canonicalUrl(existing?.url);
    if (incomingUrl && existingUrl && incomingUrl === existingUrl) return "canonical_url";
    if (!incomingUrl && !existingUrl && applicationFingerprint(incoming) === applicationFingerprint(existing)) return "company_role_location";
    return null;
  }

  function findDuplicateMatches(incoming, existing) {
    const matches = [];
    incoming.forEach((record, incomingIndex) => {
      const match = existing.find((candidate) => duplicateReason(record, candidate));
      if (match) matches.push({
        incoming_index: incomingIndex,
        existing_application_id: match.id,
        reason: duplicateReason(record, match),
        fingerprint: applicationFingerprint(record),
      });
    });
    return matches;
  }

  function toBackup(parsed, mapping) {
    const records = [];
    const allRecords = [];
    const errors = [];
    const seen = new Set();
    let duplicates = 0;
    parsed.rows.forEach((row) => {
      const company = valueFor(row, mapping, "company");
      const role = valueFor(row, mapping, "role");
      if (!company || !role) {
        errors.push(`Row ${row.rowNumber}: Company and Role are required.`);
        return;
      }
      const record = {
        company,
        role,
        location: valueFor(row, mapping, "location"),
        work_mode: normalizeChoice(valueFor(row, mapping, "work_mode"), ["Remote", "Hybrid", "On-site"], { onsite: "on-site", "on site": "on-site" }) || "Remote",
        status: normalizeChoice(valueFor(row, mapping, "status"), ["Wishlist", "Applied", "Interview", "Offer", "Rejected"]) || "Wishlist",
        source: valueFor(row, mapping, "source"),
        url: valueFor(row, mapping, "url"),
        salary_min: normalizeAmount(valueFor(row, mapping, "salary_min")),
        salary_max: normalizeAmount(valueFor(row, mapping, "salary_max")),
        salary_period: normalizeChoice(valueFor(row, mapping, "salary_period"), ["Hourly", "Monthly", "Annual"]) || "Annual",
        currency: valueFor(row, mapping, "currency").toUpperCase() || "USD",
        applied_date: valueFor(row, mapping, "applied_date") || null,
        next_action_date: valueFor(row, mapping, "next_action_date") || null,
        notes: valueFor(row, mapping, "notes"),
      };
      allRecords.push(record);
      const signature = applicationFingerprint(record);
      if (seen.has(signature)) {
        duplicates += 1;
        return;
      }
      seen.add(signature);
      records.push(record);
    });
    return { records, allRecords, errors, duplicates, totalRows: parsed.rows.length };
  }

  root.JobFlowCsv = { fields, parse, inferMapping, normalizeHeader, canonicalUrl, applicationFingerprint, duplicateReason, findDuplicateMatches, toBackup };
})();
