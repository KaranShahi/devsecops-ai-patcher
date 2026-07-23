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
    r"""(?:(?:const|let|var)\s+(?P<target>\w+)\s*=\s*)?"""
    r"""(?P<quote>["'`])(?P<body>(?:SELECT|INSERT|UPDATE|DELETE)[^"'`]*)(?P=quote)\s*\+\s*(?P<var>\w+)""",
    re.IGNORECASE,
)
CMD_PATTERN = re.compile(
    r"""exec\(\s*(?P<quote>["'`])(?P<body>[^"'`]*)(?P=quote)\s*\+\s*(?P<var>\w+)"""
)
CHILD_PROCESS_IMPORT_PATTERN = re.compile(r"""require\(\s*["']child_process["']\s*\)""")


def _to_env_name(identifier: str) -> str:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", identifier)
    snake = re.sub(r"[^A-Za-z0-9]+", "_", snake)
    return snake.upper().strip("_")


def _find_query_call(file_lines: list[str], start_idx: int, target: str) -> int | None:
    """Find the nearby `.query(target, ...)` call site that needs the bound
    parameter array, so the `?` placeholder isn't left with nothing to bind."""
    pattern = re.compile(r"""\.query\(\s*""" + re.escape(target) + r"""\s*,""")
    for i in range(start_idx, min(start_idx + 20, len(file_lines))):
        if pattern.search(file_lines[i]):
            return i
    return None


def _inject_bind_params(line: str, target: str, var: str) -> str:
    pattern = re.compile(r"""(\.query\(\s*""" + re.escape(target) + r"""\s*,\s*)""")
    return pattern.sub(lambda m: m.group(1) + f"[{var}], ", line, count=1)


def _ensure_execfile_import(file_lines: list[str]):
    """Add `execFile` to an existing destructured `child_process` import.

    Returns "already-present" if execFile is already imported, (line_index,
    new_line) if an import was found and patched, or None if no destructured
    import was found (nothing safe to rewrite automatically).
    """
    for i, line in enumerate(file_lines):
        if not CHILD_PROCESS_IMPORT_PATTERN.search(line):
            continue
        if re.search(r"\bexecFile\b", line):
            return "already-present"
        braces = re.search(r"\{([^}]*)\}", line)
        if not braces:
            return None
        names = [n.strip() for n in braces.group(1).split(",") if n.strip()]
        names.append("execFile")
        new_line = re.sub(r"\{[^}]*\}", "{ " + ", ".join(names) + " }", line, count=1)
        return i, new_line
    return None


def patch_hardcoded_secret(file_lines: list[str], idx: int, file_ext: str):
    line = file_lines[idx]
    match = SECRET_PATTERN.search(line)
    if not match:
        return None
    env_name = _to_env_name(match.group("name"))
    template = ENV_READ_TEMPLATES.get(file_ext, ENV_READ_TEMPLATES[".py"])
    replacement = f'{match.group("name")} = {template.format(name=env_name)}{match.group("trail")}'
    fixed = line[: match.start()] + replacement + line[match.end() :]
    note = f"Move the secret to an environment variable named {env_name}."
    return {idx: fixed}, note


def patch_sql_injection(file_lines: list[str], idx: int, file_ext: str):
    line = file_lines[idx]
    match = SQL_PATTERN.search(line)
    if not match:
        return None
    quote = match.group("quote")
    parameterized = f'{quote}{match.group("body")}?{quote}'
    fixed = line[: match.start()] + parameterized + line[match.end() :]
    changes = {idx: fixed}

    var = match.group("var")
    target = match.group("target")
    note = f"Pass `{var}` as a bound query parameter instead of concatenating it into the SQL string."

    if target:
        call_idx = _find_query_call(file_lines, idx, target)
        if call_idx is not None:
            changes[call_idx] = _inject_bind_params(file_lines[call_idx], target, var)
            note += f" Added `[{var}]` as the bound parameter array to the `.query()` call."
        else:
            note += (
                f" Could not find the matching `.query({target}, ...)` call "
                f"automatically — pass `[{var}]` as its second argument by hand."
            )

    return changes, note


def patch_command_injection(file_lines: list[str], idx: int, file_ext: str):
    if file_ext not in (".js", ".ts"):
        return None  # execFile is a Node API; leave other languages for manual review.

    line = file_lines[idx]
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
    changes = {idx: fixed}
    note = f"Use execFile with an argument array instead of a shell string, and validate `{var}` before use."

    import_result = _ensure_execfile_import(file_lines)
    if isinstance(import_result, tuple):
        import_idx, new_import_line = import_result
        changes[import_idx] = new_import_line
        note += " Added `execFile` to the existing `child_process` import."
    elif import_result is None:
        note += " Ensure `execFile` is imported from `child_process` in this file."

    return changes, note


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
        in_range = 0 <= idx < len(file_lines)
        result = patcher(file_lines, idx, file_ext) if patcher and in_range else None
        if result:
            changes, note = result
            for line_idx, new_line in changes.items():
                file_lines[line_idx] = new_line
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
