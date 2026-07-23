# Security Policy

This repository is a portfolio / demo project. It is not deployed anywhere
and does not process real user data.

## Scope

- `agent.py` and `scanner.py` are the actual tool under test.
- `vulnerable_app/` is **intentionally vulnerable** on purpose — it exists
  as a fixed scan target and test fixture. Findings against it are expected,
  not bugs. Do not deploy it anywhere reachable.

## Reporting an issue

If you find a bug in the scanner or patch templates themselves (a false
negative, a patch that produces broken or unsafe output, etc.), please open
a GitHub issue on this repository describing the input that triggers it and
the incorrect output produced. There is no bounty program — this is a
learning project, not a production service.
