# Screenshots

`jobflow-dashboard.png` is a static visual snapshot of the seeded browser
demo. It contains fictional companies only and is included to make the
repository easy to scan on GitHub. It is not a release/version contract: the
running footer and `/api/health` response are the source of truth for the
current build.

To refresh the image after a UI change, run JobFlow with a fresh database, set
the browser viewport near 1440 × 900, and save the unfiltered dashboard as
`jobflow-dashboard.png` in this directory. Before publishing, verify that the
image contains only the fictional demo companies shipped in
`jobflow/database.py` and no personal application data.
