"""
Autonomous DevSecOps Static Analysis & Patching Engine.

Pipeline:
  1. Run a lightweight static scan against the target codebase.
  2. Save raw findings as JSON (scan_results.json).
  3. Apply deterministic, rule-based patches directly to the source files
     using pattern-specific remediation templates — parameterized queries,
     environment-based secrets, and safe subprocess APIs.
  4. Save a changelog of every applied fix as pr_output.md.

Source files are rewritten in place. Run it inside a git checkout so the
changes are reviewable with `git diff` before you commit them. The whole
pipeline runs offline — no network calls, no API keys, no third-party
services.
"""

import json
import os
import re
from pathlib import Path

from scanner import scan_directory

TARGET_DIR = os.environ.get("TARGET_DIR", "vulnerable_app")
SCAN_OUTPUT = "scan_results.json"
PR_OUTPUT = "pr_output.md"

SEVERITY_PRIORITY = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P2"}

ENV_READ_TEMPLATES = {
    ".js": "process.env.{name}",
    ".ts": "process.env.{name}",
    ".py": 'os.getenv("{name}")',
    ".rb": 'ENV["{name}"]',
    ".go": "os.Getenv(\"{name}\")",
    ".java": 'System.getenv("{name}")',
}

SECRET_PATTERN = re.compile(
    r"""(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<quote>["'])"""
    r"""(?:sk_live_|AKIA|[A-Za-z0-9_\-]{16,})(?P=quote)(?P<trail>;?)"""
)
SQL_PATTERN = re.compile(
    r"""(?P<quote>["'`])(?P<body>(?:SELECT|INSERT|UPDATE|DELETE)[^"'`]*)(?P=quote)\s*\+\s*(?P<var>\w+)""",
    re.IGNORECASE,
)
CMD_PATTERN = re.compile(
    r"""exec\(\s*(?P<quote>["'`])(?P<body>[^"'`]*)(?P=quote)\s*\+\s*(?P<var>\w+)"""
)


def _to_env_name(identifier: str) -> str:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", identifier)
    snake = re.sub(r"[^A-Za-z0-9]+", "_", snake)
    return snake.upper().strip("_")


def patch_hardcoded_secret(line: str, file_ext: str) -> tuple[str, str] | None:
    match = SECRET_PATTERN.search(line)
    if not match:
        return None
    env_name = _to_env_name(match.group("name"))
    template = ENV_READ_TEMPLATES.get(file_ext, ENV_READ_TEMPLATES[".py"])
    replacement = f'{match.group("name")} = {template.format(name=env_name)}{match.group("trail")}'
    fixed = line[: match.start()] + replacement + line[match.end() :]
    note = f"Move the secret to an environment variable named {env_name}."
    return fixed, note


def patch_sql_injection(line: str, file_ext: str) -> tuple[str, str] | None:
    match = SQL_PATTERN.search(line)
    if not match:
        return None
    quote = match.group("quote")
    parameterized = f'{quote}{match.group("body")}?{quote}'
    fixed = line[: match.start()] + parameterized + line[match.end() :]
    note = f"Pass `{match.group('var')}` as a bound query parameter instead of concatenating it into the SQL string."
    return fixed, note


def patch_command_injection(line: str, file_ext: str) -> tuple[str, str] | None:
    match = CMD_PATTERN.search(line)
    if not match:
        return None
    tokens = match.group("body").strip().split()
    if not tokens:
        return None
    cmd, *static_args = tokens
    var = match.group("var")
    args = ", ".join([f'"{a}"' for a in static_args] + [var])
    fixed = line[: match.start()] + f'execFile("{cmd}", [{args}]' + line[match.end() :]
    note = f"Use execFile with an argument array instead of a shell string, and validate `{var}` before use."
    return fixed, note


PATCHERS = {
    "hardcoded-secret": patch_hardcoded_secret,
    "sql-injection": patch_sql_injection,
    "command-injection": patch_command_injection,
}


def patch_file(file_path: str, findings: list[dict]) -> list[dict]:
    path = Path(file_path)
    file_ext = path.suffix
    file_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)

    results = []
    for finding in findings:
        patcher = PATCHERS.get(finding["rule_id"])
        idx = finding["line"] - 1
        original_line = file_lines[idx] if 0 <= idx < len(file_lines) else None
        result = patcher(original_line, file_ext) if patcher and original_line is not None else None
        if result:
            file_lines[idx], note = result
            results.append({**finding, "applied": True, "fixed_snippet": file_lines[idx].strip(), "remediation_note": note})
        else:
            results.append({**finding, "applied": False, "fixed_snippet": None, "remediation_note": None})

    path.write_text("".join(file_lines), encoding="utf-8")
    return results


def build_report(patched_findings: list[dict]) -> str:
    applied_count = sum(1 for f in patched_findings if f["applied"])
    lines = [
        "# Patch Report",
        "",
        f"{len(patched_findings)} finding(s) scanned, {applied_count} patched in place.\n",
    ]

    for f in patched_findings:
        priority = SEVERITY_PRIORITY.get(f["severity"], "P2")
        status = "PATCHED" if f["applied"] else "MANUAL REVIEW NEEDED"
        lines.append(f"## [{priority}] {f['rule_id']} — {f['file']}:{f['line']} ({f['cwe']}) — {status}")
        lines.append(f"{f['description']}\n")
        lines.append("```diff")
        lines.append(f"- {f['snippet']}")
        if f["fixed_snippet"]:
            lines.append(f"+ {f['fixed_snippet']}")
        lines.append("```")
        if f["remediation_note"]:
            lines.append(f"\n{f['remediation_note']}\n")
        else:
            lines.append("\nNo automated pattern match for this finding — the file was left unchanged.\n")

    lines.append("---\n")
    lines.append(f"## Summary\n\n{applied_count} of {len(patched_findings)} finding(s) were patched directly in the source files.")
    lines.append("Review the changes with `git diff` before committing.\n")
    return "\n".join(lines)


def main() -> None:
    print(f"[1/3] Scanning '{TARGET_DIR}' ...")
    findings = scan_directory(TARGET_DIR)
    print(f"      Found {len(findings)} issue(s).")

    print(f"[2/3] Saving findings to {SCAN_OUTPUT} ...")
    Path(SCAN_OUTPUT).write_text(json.dumps(findings, indent=2), encoding="utf-8")

    if not findings:
        print("No findings — nothing to patch. Exiting.")
        return

    print("[3/3] Applying deterministic rule-based patches to source files ...")
    findings_by_file: dict[str, list[dict]] = {}
    for finding in findings:
        findings_by_file.setdefault(finding["file"], []).append(finding)

    patched = []
    for file_path, file_findings in findings_by_file.items():
        patched.extend(patch_file(file_path, file_findings))

    applied_count = sum(1 for f in patched if f["applied"])
    Path(PR_OUTPUT).write_text(build_report(patched), encoding="utf-8")

    print(f"      Patched {applied_count} of {len(patched)} finding(s) in place.")
    print("\nDone. Review the patch report at:", PR_OUTPUT)


if __name__ == "__main__":
    main()
