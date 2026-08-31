# Security policy

JobFlow is a local portfolio application and does not implement authentication or multi-user isolation. Do not expose a local instance to the public internet or store real applicant data in the demo database.

If you identify a security issue in the request parser, static-file handling, or SQLite access layer, please avoid publishing exploit details in an issue. Contact the repository owner privately with a minimal reproduction and impact summary.

Application materials are metadata only. JobFlow does not upload, read, or
serve local files; material links must use `http` or `https` and are opened in
a separate browser context with `noreferrer`. Submission snapshots copy the
metadata needed for audit history and do not grant access to the linked
resource.

API failures return a request id for support correlation, but intentionally omit
filesystem paths, SQL details, and stack traces. Treat request ids as diagnostic
metadata rather than authentication tokens. The browser demo stores synthetic
records in local storage; do not import real applicant data into a public demo
page.
