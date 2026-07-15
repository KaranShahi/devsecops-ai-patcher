# DevSecOps Automated Static Analysis & Patching Engine

A tool that scans a codebase for common vulnerability patterns, then
applies deterministic, rule-based patches directly to the source files —
no external calls, no manual editing.

**It really patches your code.** `agent.py` rewrites the affected lines
in place and writes a changelog of every fix to `pr_output.md`. Run it
inside a git checkout so `git diff` shows you exactly what changed before
you commit.

The entire pipeline runs offline: no network calls, no API keys, no
third-party services. Every fix is produced by a pattern-specific regex
transform, not a model — the same input always produces the same output,
and re-running the tool on already-patched code is a no-op.

## How it works

```
vulnerable_app/  --scan-->  scanner.py  --JSON-->  scan_results.json
                                                          |
                                                          v
                                                agent.py matches each
                                                finding's rule id to a
                                                regex-based patch template
                                                and rewrites the file
                                                          |
                                                          v
                                                pr_output.md (changelog)
```

- `scanner.py` — a small regex-based static scanner (hardcoded secrets,
  SQL injection, command injection), each rule tagged with a CWE id.
- `vulnerable_app/server.js` — an intentionally vulnerable Express app used
  as the scan target.
- `agent.py` — orchestrates the scan and applies a rule-specific patch
  directly to each affected file, in place:
  - `hardcoded-secret` → replaces the literal value with an environment
    variable read (`process.env.X`, `os.getenv("X")`, etc., chosen by
    file extension).
  - `sql-injection` → replaces the string-concatenated variable with a
    `?` placeholder for a bound query parameter.
  - `command-injection` → rewrites a shell `exec("cmd " + var)` call into
    `execFile("cmd", [var])`, dropping the shell string entirely.

Each patch template only fires on the exact pattern shape its scanner rule
detects — it's a targeted, explainable transform, not a general-purpose
code rewriter. Findings that don't match a known pattern are left
untouched in the file and flagged in the report for manual review.

## Setup

No external dependencies — the whole tool runs on the Python standard
library (`re`, `json`, `pathlib`). Python 3.10+ is all you need.

Optional override:
- `TARGET_DIR` environment variable — defaults to `vulnerable_app`.

## Running the agent

```bash
python agent.py
```

This will:
1. Scan `vulnerable_app/` and print how many issues were found.
2. Save raw findings to `scan_results.json`.
3. Apply a deterministic patch template to each finding, rewriting the
   affected source file(s) in place.
4. Save a changelog of every applied fix to `pr_output.md`.

Run `git diff` afterward to see exactly what was changed, or open
`pr_output.md` for a per-finding summary (rule, CWE, before/after, and
which findings still need manual review).

Running the tool again on already-patched code finds nothing to fix —
the patches are idempotent.

## Running just the scanner

```bash
python scanner.py vulnerable_app
```

Prints findings as JSON to stdout without applying any patches.
