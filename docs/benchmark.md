# Performance benchmark

[`scripts/benchmark.py`](../scripts/benchmark.py) measures the four read paths
that drive the dashboard and application workspace:

- filtered application list;
- Today action feed;
- one application workspace snapshot;
- historical Insights.

The default fixture contains 5,000 applications, 25,000 events, and 10,000
tasks. It is generated in a temporary directory and removed at the end of the
run. The user's database is never opened.

```bash
python3 scripts/benchmark.py --iterations 7
```

The output includes the Python/SQLite runtime, fixture counts, and the observed
minimum, median, maximum, and p95 latency in milliseconds. Use `--json` for a
machine-readable report, or reduce `--applications`,
`--events-per-application`, and `--tasks-per-application` for a quick smoke
check.

The repository deliberately does not publish a performance threshold. Results
depend on the machine, filesystem, Python build, and SQLite version; a future
release may add a threshold only after collecting comparable measurements.
