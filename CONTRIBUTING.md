# Contributing

Thanks for taking an interest in JobFlow. The project favors small, well-tested changes over new dependencies.

## Local checklist

1. Run `python3 -m unittest discover -s tests -v`.
2. Run `python3 -m compileall -q app.py jobflow tests`.
3. Start the app with a temporary database and verify the affected API/UI path.
4. Keep user-provided values validated at the server boundary and SQL values parameterized.

## Design expectations

- Preserve the dependency-free Python standard-library runtime.
- Add regression coverage for validation, persistence, or HTTP behavior that changes.
- Do not commit generated SQLite databases or personally identifiable application data.
