# DevSecOps Automated Static Analysis & Patching Engine

![CI](https://github.com/KaranShahi/devsecops-ai-patcher/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-green)

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
  - `sql-injection` → replaces the string-concatenated query with a `?`
    placeholder, then locates the nearby `.query(name, ...)` call and
    adds the real value as a bound parameter array — the placeholder
    isn't left with nothing to bind it. If the call site can't be found
    within a few lines, the finding is still marked for manual
    verification in the report rather than silently claiming success.
  - `command-injection` → rewrites a shell `exec("cmd " + var)` call into
    `execFile("cmd", [var], ...)`, and adds `execFile` to the file's
    existing `child_process` import if it isn't already destructured
    there. JS/TS only — other languages are left for manual review.

Each patch template only fires on the exact pattern shape its scanner rule
detects — it's a targeted, explainable transform, not a general-purpose
code rewriter. Findings that don't match a known pattern are left
untouched in the file and flagged in the report for manual review.

## Why these vulnerabilities matter

- **Hardcoded secrets (CWE-798)** ship with every clone of the repo and
  persist in git history even after the line is deleted or rotated — one
  of the most common ways an "internal-only" key ends up in a breach
  dump.
- **SQL injection (CWE-89)** lets an attacker read, modify, or delete
  arbitrary rows by controlling the query itself, not just its inputs —
  historically one of the most common root causes of large-scale data
  breaches.
- **Command injection (CWE-78)** hands an attacker arbitrary shell
  execution on the host running the app: full compromise of that
  machine, not just the data behind it.

## Setup

No external dependencies for the tool itself — `agent.py` and
`scanner.py` run on the Python standard library (`re`, `json`,
`pathlib`). Python 3.10+ is all you need.

Optional override:
- `TARGET_DIR` environment variable — defaults to `vulnerable_app`.

To actually run the intentionally-vulnerable demo app (not required to
run the scanner or patcher, only if you want to see it live):

```bash
cd vulnerable_app
npm install
npm start
```

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

## Running the tests

The test suite is standard library too — no pytest, no extra installs.

```bash
python -m unittest discover -v
```

`tests/test_scanner.py` checks the scanner rules fire (and only fire) on
the seeded vulnerabilities. `tests/test_agent.py` runs the full
scan → patch pipeline against a scratch copy of `vulnerable_app/` and
verifies the patched output actually works, not just that it looks fixed
in a diff: the SQL fix's bound parameter reaches `db.query()`, the
command-injection fix's `execFile` call has a matching import, the
patched file still parses as valid JavaScript (when `node` is on `PATH`),
and re-running the patcher against already-patched code is a true no-op.
CI runs this suite on every push and pull request.

## Known limitations

- The scanner is three regex rules, not an AST-aware SAST engine — it
  will miss variations in whitespace, template literals, ORM query
  builders, and non-JS-shaped equivalents of these patterns. It exists to
  give the patching pipeline concrete, labeled findings to work from, not
  to replace Semgrep/CodeQL/etc. in a real pipeline.
- The `command-injection` patch template is JavaScript/TypeScript-specific
  (it emits `execFile`); matching findings in other scanned languages are
  left for manual review instead of guessing at that language's
  equivalent API.
- All three patch templates locate related lines (an import, a sibling
  `.query()` call) by nearby-line proximity, not by parsing scope. In
  unusually structured code they fall back to flagging the finding for
  manual review rather than silently emitting a wrong fix.

## License

[MIT](LICENSE)
