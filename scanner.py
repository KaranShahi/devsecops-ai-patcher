"""
Lightweight regex-based static scanner.

Not a replacement for a real SAST tool (Semgrep, CodeQL, etc.) — this exists
so the agent has something concrete and fast to run for the demo pipeline.
Each rule maps to a CWE so the patching step has real signal to work with.
"""

import re
from dataclasses import dataclass, asdict
from pathlib import Path

RULES = [
    {
        "id": "hardcoded-secret",
        "cwe": "CWE-798",
        "severity": "critical",
        "description": "Hardcoded credential or API key found in source.",
        "pattern": re.compile(
            r"""(?i)(api[_-]?key|secret|token|password)\s*=\s*["'](sk_live_|AKIA|[A-Za-z0-9_\-]{16,})["']"""
        ),
    },
    {
        "id": "sql-injection",
        "cwe": "CWE-89",
        "severity": "critical",
        "description": "SQL query built via string concatenation with untrusted input.",
        "pattern": re.compile(
            r"""(SELECT|INSERT|UPDATE|DELETE)[^"'`]*["'`]+\s*\+\s*\w+""", re.IGNORECASE
        ),
    },
    {
        "id": "command-injection",
        "cwe": "CWE-78",
        "severity": "high",
        "description": "Shell command built via string concatenation with untrusted input.",
        "pattern": re.compile(
            r"""exec\(\s*["'`][^"'`]*["'`]\s*\+\s*\w+"""
        ),
    },
]

SCANNABLE_EXTENSIONS = {".js", ".ts", ".py", ".java", ".go", ".rb"}


@dataclass
class Finding:
    rule_id: str
    cwe: str
    severity: str
    description: str
    file: str
    line: int
    snippet: str


def scan_file(path: Path) -> list[Finding]:
    findings = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            if rule["pattern"].search(line):
                findings.append(
                    Finding(
                        rule_id=rule["id"],
                        cwe=rule["cwe"],
                        severity=rule["severity"],
                        description=rule["description"],
                        file=str(path),
                        line=lineno,
                        snippet=line.strip(),
                    )
                )
    return findings


def scan_directory(target_dir: str) -> list[dict]:
    root = Path(target_dir)
    all_findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in SCANNABLE_EXTENSIONS:
            all_findings.extend(scan_file(path))
    return [asdict(f) for f in all_findings]


if __name__ == "__main__":
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "vulnerable_app"
    results = scan_directory(target)
    print(json.dumps(results, indent=2))
