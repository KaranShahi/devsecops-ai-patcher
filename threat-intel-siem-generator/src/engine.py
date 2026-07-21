"""
engine.py — Detection Engine & Orchestration

Acts as a Senior Detection Engineer: takes an EnrichedVulnerability from
enricher.py and produces:
  - A Sigma rule targeting Windows Event Logs / Sysmon (process_creation,
    security logon events, file events, etc.)
  - A Sigma rule targeting Linux auditd
  - A YARA rule for static/heuristic artifact matching

Both Sigma rules are written as one multi-document YAML file per CVE
(rules/<CVE-ID>_sigma.yml); the YARA rule is written to rules/<CVE-ID>.yar.

Rule content starts from deterministic, category-specific templates built
from real detection-engineering practice (e.g. spoolsv.exe spawning a child
process for PrintNightmare-class bugs, lsass.exe spawning a child process
for SMB memory-corruption RCE, w3wp.exe spawning a shell for web RCE/SSRF
chains). This guarantees valid output with zero external dependencies.

If ANTHROPIC_API_KEY is configured and USE_LLM=true, an optional refinement
pass sends the draft rules + enrichment context to Claude to tighten
selection logic and false-positive notes. The LLM pass is purely additive:
if it's unavailable, errors, or returns invalid YAML, the deterministic
draft is used as-is and the run continues without interruption.
"""

from __future__ import annotations

import argparse
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from enricher import EnrichedVulnerability, enrich, load_config as load_enrich_config
from ingestor import get_recent_vulnerabilities

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_TIMESTAMP = datetime.now(timezone.utc)

# -----------------------------------------------------------------------------
# Known real-world parent-process indicators, keyed by keyword found in the
# vendor/product/vulnerability name. These are well-documented exploitation
# patterns (e.g. spoolsv.exe spawning children is the canonical PrintNightmare
# indicator), not guesses — using them keeps generated rules tightly scoped.
# -----------------------------------------------------------------------------
PRODUCT_PROCESS_HINTS: list[tuple[str, str]] = [
    ("log4j", "java.exe"),
    ("jboss", "java.exe"),
    ("confluence", "java.exe"),
    ("jira", "java.exe"),
    ("tomcat", "java.exe"),
    ("print spooler", "spoolsv.exe"),
    ("exchange", "w3wp.exe"),
    ("sharepoint", "w3wp.exe"),
    ("iis", "w3wp.exe"),
    ("smb", "lsass.exe"),
]


def _guess_parent_process(record_text: str) -> str | None:
    haystack = record_text.lower()
    for keyword, process in PRODUCT_PROCESS_HINTS:
        if keyword in haystack:
            return process
    return None


# -----------------------------------------------------------------------------
# Windows Sigma templates, one per detection category
# -----------------------------------------------------------------------------
def _windows_template(category: str, parent_hint: str | None) -> dict[str, Any]:
    shell_children = [
        "\\cmd.exe", "\\powershell.exe", "\\pwsh.exe", "\\mshta.exe",
        "\\wscript.exe", "\\cscript.exe", "\\certutil.exe", "\\rundll32.exe",
    ]

    if category in ("command_execution", "deserialization", "input_validation"):
        parents = [f"\\{parent_hint}"] if parent_hint else [
            "\\w3wp.exe", "\\httpd.exe", "\\nginx.exe", "\\java.exe", "\\tomcat.exe",
        ]
        return {
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {
                "selection": {
                    "ParentImage|endswith": parents,
                    "Image|endswith": shell_children,
                },
                "condition": "selection",
            },
            "falsepositives": [
                "Legitimate administrative scripts launched by the parent process",
                "Software updaters or health-check probes that shell out to command interpreters",
            ],
            "level": "high",
        }

    if category == "privilege_escalation":
        if parent_hint == "spoolsv.exe":
            return {
                "logsource": {"category": "process_creation", "product": "windows"},
                "detection": {
                    "selection": {
                        "ParentImage|endswith": "\\spoolsv.exe",
                        "Image|endswith": ["\\cmd.exe", "\\powershell.exe", "\\rundll32.exe", "\\regsvr32.exe"],
                    },
                    "condition": "selection",
                },
                "falsepositives": ["Legitimate printer driver installation by administrators"],
                "level": "high",
            }
        return {
            "logsource": {"category": "process_creation", "product": "windows", "service": "security"},
            "detection": {
                "selection": {
                    "EventID": 4673,
                    "PrivilegeList|contains": ["SeDebugPrivilege", "SeLoadDriverPrivilege", "SeTcbPrivilege"],
                },
                "condition": "selection",
            },
            "falsepositives": ["Backup software and system utilities that legitimately require these privileges"],
            "level": "medium",
        }

    if category == "memory_corruption_rce":
        return {
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {
                "selection": {
                    "ParentImage|endswith": "\\lsass.exe",
                    "Image|endswith": shell_children,
                },
                "condition": "selection",
            },
            "falsepositives": ["Extremely rare: lsass.exe should almost never spawn child processes"],
            "level": "critical",
        }

    if category == "ssrf":
        parents = [f"\\{parent_hint}"] if parent_hint else ["\\w3wp.exe", "\\httpd.exe", "\\nginx.exe"]
        return {
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {
                "selection": {
                    "ParentImage|endswith": parents,
                    "Image|endswith": ["\\cmd.exe", "\\powershell.exe", "\\certutil.exe"],
                },
                "condition": "selection",
            },
            "falsepositives": ["Custom web server modules/extensions that legitimately spawn helper processes"],
            "level": "high",
        }

    if category == "auth_bypass":
        return {
            "logsource": {"product": "windows", "service": "security"},
            "detection": {
                "selection": {"EventID": 4625},
                "timeframe": "5m",
                "condition": "selection | count(TargetUserName) by IpAddress > 10",
            },
            "falsepositives": ["Misconfigured service accounts", "Password rotation causing transient failures"],
            "level": "medium",
        }

    if category == "credential_access":
        return {
            "logsource": {"product": "windows", "service": "security"},
            "detection": {
                "selection": {"EventID": 4648},
                "condition": "selection",
            },
            "falsepositives": ["Scheduled tasks configured with explicit credentials", "Administrative RunAs usage"],
            "level": "medium",
        }

    if category == "path_traversal":
        return {
            "logsource": {"category": "file_event", "product": "windows"},
            "detection": {
                "selection": {"TargetFilename|contains": ["..\\", "../"]},
                "condition": "selection",
            },
            "falsepositives": ["Archive extraction utilities that legitimately use relative paths"],
            "level": "medium",
        }

    # resource_exhaustion / unmapped fallback
    return {
        "logsource": {"category": "process_creation", "product": "windows"},
        "detection": {
            "selection": {"CommandLine|contains": ["--max-requests", "flood", "slowloris"]},
            "condition": "selection",
        },
        "falsepositives": ["Legitimate load-testing tools"],
        "level": "low",
    }


# -----------------------------------------------------------------------------
# Linux auditd Sigma templates, one per detection category
# -----------------------------------------------------------------------------
def _linux_auditd_template(category: str) -> dict[str, Any]:
    if category in ("command_execution", "deserialization", "input_validation"):
        return {
            "logsource": {"product": "linux", "service": "auditd"},
            "detection": {
                "selection": {"type": "EXECVE", "a0|contains": ["/bin/sh", "/bin/bash", "/bin/dash"]},
                "condition": "selection",
            },
            "falsepositives": ["Legitimate shell scripts invoked by the application for maintenance tasks"],
            "level": "high",
        }

    if category == "privilege_escalation":
        return {
            "logsource": {"product": "linux", "service": "auditd"},
            "detection": {
                "selection": {"type": "SYSCALL", "syscall": ["setuid", "setgid"], "success": "yes"},
                "condition": "selection",
            },
            "falsepositives": ["Standard privilege-dropping behavior of daemons at startup"],
            "level": "medium",
        }

    if category == "memory_corruption_rce":
        return {
            "logsource": {"product": "linux", "service": "auditd"},
            "detection": {
                "selection": {"type": "ANOM_ABEND"},
                "condition": "selection",
            },
            "falsepositives": ["Software bugs / crashes unrelated to exploitation attempts"],
            "level": "medium",
        }

    if category == "ssrf":
        return {
            "logsource": {"product": "linux", "service": "auditd"},
            "detection": {
                "selection": {"type": "SYSCALL", "syscall": "connect"},
                "condition": "selection",
            },
            "falsepositives": [
                "auditd has limited network context on its own; this rule is intentionally broad — "
                "pair with Zeek/Suricata network telemetry before alerting in production",
            ],
            "level": "low",
        }

    if category == "auth_bypass":
        return {
            "logsource": {"product": "linux", "service": "auditd"},
            "detection": {
                "selection": {"type": ["USER_AUTH", "USER_LOGIN"], "res": "success"},
                "condition": "selection",
            },
            "falsepositives": ["Normal successful authentications — correlate with anomalous source/time before alerting"],
            "level": "low",
        }

    if category == "credential_access":
        return {
            "logsource": {"product": "linux", "service": "auditd"},
            "detection": {
                "selection": {
                    "type": "SYSCALL",
                    "syscall": ["open", "openat"],
                    "path|contains": ["/etc/shadow", "/etc/passwd", ".ssh/id_rsa"],
                },
                "condition": "selection",
            },
            "falsepositives": ["Backup jobs or configuration management tools reading these paths"],
            "level": "high",
        }

    if category == "path_traversal":
        return {
            "logsource": {"product": "linux", "service": "auditd"},
            "detection": {
                "selection": {"type": "PATH", "name|contains": "../"},
                "condition": "selection",
            },
            "falsepositives": ["Legitimate relative path usage in application logic"],
            "level": "medium",
        }

    return {
        "logsource": {"product": "linux", "service": "auditd"},
        "detection": {
            "selection": {"type": "SYSCALL", "syscall": ["fork", "clone"]},
            "condition": "selection",
        },
        "falsepositives": ["Normal high-concurrency workloads"],
        "level": "low",
    }


def _sigma_uuid(cve_id: str, platform: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{cve_id}-{platform}-sigma"))


def build_sigma_rules(enriched: EnrichedVulnerability) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build (windows_rule, linux_auditd_rule) Sigma dicts for one CVE."""
    rec = enriched.base
    parent_hint = _guess_parent_process(f"{rec.vendor} {rec.product} {rec.vulnerability_name}")
    tags = [f"attack.{enriched.mitre_technique.lower()}", f"cve.{rec.cve_id.lower().replace('-', '_')}"]

    win_template = _windows_template(enriched.category, parent_hint)
    windows_rule = {
        "title": f"Possible {rec.vulnerability_name or rec.cve_id} Exploitation Activity",
        "id": _sigma_uuid(rec.cve_id, "windows"),
        "status": "experimental",
        "description": (
            f"Detects process/logon telemetry consistent with exploitation of {rec.cve_id} "
            f"({rec.vendor} {rec.product}), classified as {enriched.category} "
            f"(MITRE ATT&CK {enriched.mitre_technique} - {enriched.mitre_name})."
        ),
        "references": enriched.references or [f"https://nvd.nist.gov/vuln/detail/{rec.cve_id}"],
        "author": "AI-Driven Threat Intel & SIEM Detection Rule Generator",
        "date": RUN_TIMESTAMP.strftime("%Y/%m/%d"),
        "tags": tags,
        **win_template,
    }

    linux_template = _linux_auditd_template(enriched.category)
    linux_rule = {
        "title": f"Possible {rec.vulnerability_name or rec.cve_id} Exploitation Activity (Linux auditd)",
        "id": _sigma_uuid(rec.cve_id, "linux"),
        "status": "experimental",
        "description": (
            f"Detects auditd telemetry consistent with exploitation of {rec.cve_id} "
            f"({rec.vendor} {rec.product}), classified as {enriched.category} "
            f"(MITRE ATT&CK {enriched.mitre_technique} - {enriched.mitre_name})."
        ),
        "references": enriched.references or [f"https://nvd.nist.gov/vuln/detail/{rec.cve_id}"],
        "author": "AI-Driven Threat Intel & SIEM Detection Rule Generator",
        "date": RUN_TIMESTAMP.strftime("%Y/%m/%d"),
        "tags": tags,
        **linux_template,
    }

    return windows_rule, linux_rule


def validate_sigma_yaml(yaml_text: str) -> bool:
    """Confirm the generated Sigma YAML round-trips through a parser cleanly."""
    try:
        list(yaml.safe_load_all(yaml_text))
        return True
    except yaml.YAMLError as exc:
        logger.error("Generated Sigma YAML failed validation: %s", exc)
        return False


# -----------------------------------------------------------------------------
# YARA generation
# -----------------------------------------------------------------------------
# Category -> (confidence, condition_expr, [(var_name, string_literal), ...])
# Strings are real, publicly documented exploitation indicators where they
# exist (e.g. the Log4Shell JNDI prefix, SSRF cloud-metadata targets), not
# fabricated payload hashes. Categories with no reliable static signature
# (memory corruption, auth bypass, resource exhaustion) are marked low
# confidence and say so explicitly in rule metadata.
YARA_CATEGORY_SIGNATURES: dict[str, dict[str, Any]] = {
    "deserialization": {
        "confidence": "medium",
        "condition": "any of them",
        "strings": [
            ("jndi_ldap", '"${jndi:ldap://"'),
            ("jndi_rmi", '"${jndi:rmi://"'),
            ("jndi_dns", '"${jndi:dns://"'),
            ("jndi_obfuscated", '"${${lower:j}ndi:"'),
        ],
    },
    "command_execution": {
        "confidence": "low",
        "condition": "3 of them",
        "strings": [
            ("shell_exec1", '"Runtime.getRuntime().exec"'),
            ("shell_exec2", '"ProcessBuilder("'),
            ("shell_exec3", '"cmd.exe /c "'),
            ("shell_exec4", '"powershell -enc"'),
            ("shell_exec5", '"base64_decode("'),
            ("shell_exec6", '"eval("'),
        ],
    },
    "input_validation": {
        "confidence": "low",
        "condition": "3 of them",
        "strings": [
            ("shell_exec1", '"Runtime.getRuntime().exec"'),
            ("shell_exec2", '"ProcessBuilder("'),
            ("shell_exec3", '"cmd.exe /c "'),
            ("shell_exec4", '"powershell -enc"'),
        ],
    },
    "privilege_escalation": {
        "confidence": "medium",
        "condition": "all of them",
        "strings": [
            ("printer_api", '"AddPrinterDriver"'),
            ("spool_path", "\"\\\\spool\\\\drivers\\\\\""),
        ],
    },
    "ssrf": {
        "confidence": "medium",
        "condition": "any of them",
        "strings": [
            ("scheme_file", '"file:///"'),
            ("scheme_gopher", '"gopher://"'),
            ("scheme_dict", '"dict://"'),
            ("cloud_metadata", '"169.254.169.254"'),
        ],
    },
    "path_traversal": {
        "confidence": "medium",
        "condition": "any of them",
        "strings": [
            ("dotdot_encoded", '"..%2f"'),
            ("dotdot_encoded2", '"%2e%2e%2f"'),
            ("dotdot_unix", '"../../../"'),
            ("dotdot_win", '"..\\\\..\\\\..\\\\"'),
        ],
    },
    "credential_access": {
        "confidence": "low",
        "condition": "any of them",
        "strings": [
            ("shadow_path", '"/etc/shadow"'),
            ("ssh_key_path", '".ssh/id_rsa"'),
        ],
    },
    "memory_corruption_rce": {
        "confidence": "low",
        "condition": "any of them",
        "strings": [
            ("smb_pipe", '"\\\\PIPE\\\\srvsvc"'),
        ],
    },
    "auth_bypass": {
        "confidence": "low",
        "condition": "any of them",
        "strings": [
            ("generic_bypass", '"admin=true"'),
        ],
    },
    "resource_exhaustion": {
        "confidence": "low",
        "condition": "any of them",
        "strings": [
            ("slowloris", '"slowloris"'),
        ],
    },
}


def build_yara_rule(enriched: EnrichedVulnerability) -> str:
    rec = enriched.base
    rule_name = f"{rec.cve_id.replace('-', '_')}_{enriched.category}"
    sig = YARA_CATEGORY_SIGNATURES.get(enriched.category, YARA_CATEGORY_SIGNATURES["input_validation"])

    string_lines = "\n".join(
        f"        ${name} = {literal} ascii wide nocase" for name, literal in sig["strings"]
    )

    fp_note = (
        "Static string matching cannot reliably confirm memory-corruption exploitation; "
        "use this rule only as a weak corroborating signal alongside network/behavioral detection."
        if enriched.category == "memory_corruption_rce"
        else "Tune string set against your own application logs before enabling blocking actions."
    )

    return f"""rule {rule_name}
{{
    meta:
        cve = "{rec.cve_id}"
        vendor_product = "{rec.vendor} {rec.product}"
        description = "Heuristic indicators associated with {enriched.category} exploitation of {rec.cve_id}"
        category = "{enriched.category}"
        mitre_technique = "{enriched.mitre_technique}"
        confidence = "{sig['confidence']}"
        author = "AI-Driven Threat Intel & SIEM Detection Rule Generator"
        date = "{RUN_TIMESTAMP.strftime('%Y-%m-%d')}"
        false_positive_notes = "{fp_note}"

    strings:
{string_lines}

    condition:
        {sig['condition']}
}}
"""


# -----------------------------------------------------------------------------
# Optional LLM refinement pass
# -----------------------------------------------------------------------------
def construct_llm_prompt(enriched: EnrichedVulnerability, windows_yaml: str, linux_yaml: str, yara_text: str) -> str:
    rec = enriched.base
    return f"""You are a Senior Detection Engineer reviewing draft SIEM detection content.

Vulnerability: {rec.cve_id} — {rec.vulnerability_name}
Vendor/Product: {rec.vendor} / {rec.product}
Category: {enriched.category}
MITRE ATT&CK: {enriched.mitre_technique} ({enriched.mitre_name})
CVSS: {enriched.cvss_score} ({enriched.cvss_severity})
Description: {rec.short_description}

Draft Windows Sigma rule (YAML):
---
{windows_yaml}
---

Draft Linux auditd Sigma rule (YAML):
---
{linux_yaml}
---

Draft YARA rule:
---
{yara_text}
---

Task: Tighten these rules to minimize false positives while preserving true-positive
coverage. You may adjust selection logic, add exclusions, or refine falsepositives
notes. Keep both Sigma rules syntactically valid per the Sigma specification, and
keep the YARA rule syntactically valid.

Respond with ONLY the two Sigma rules as a single YAML document stream (separated by
`---`), followed by a line containing exactly `===YARA===`, followed by the YARA rule
text. No commentary, no markdown code fences.
"""


def refine_with_llm(prompt: str, cfg: dict[str, Any]) -> str | None:
    """
    Send the draft rules to Claude for refinement. Returns the raw response text,
    or None on any failure (missing key, missing package, API error) so the
    caller can fall back to the deterministic draft without interrupting the run.
    """
    api_key = os.getenv(cfg["engine"]["anthropic_api_key_env"])
    if not api_key:
        logger.info("No ANTHROPIC_API_KEY set; skipping LLM refinement pass.")
        return None

    try:
        import anthropic
    except ImportError:
        logger.warning("`anthropic` package not installed; skipping LLM refinement pass.")
        return None

    model = os.getenv(cfg["engine"]["anthropic_model_env"]) or cfg["engine"]["default_anthropic_model"]
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=cfg["engine"]["llm_max_tokens"],
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as exc:  # noqa: BLE001 - any SDK/network failure should fall back, not crash the pipeline
        logger.warning("LLM refinement call failed (%s); using deterministic draft instead.", exc)
        return None


def _try_apply_llm_refinement(
    enriched: EnrichedVulnerability,
    windows_rule: dict[str, Any],
    linux_rule: dict[str, Any],
    yara_text: str,
    cfg: dict[str, Any],
) -> tuple[str, str]:
    """Returns (sigma_yaml_text, yara_text), refined by the LLM if possible/valid."""
    draft_sigma_yaml = yaml.dump_all([windows_rule, linux_rule], sort_keys=False, default_flow_style=False)

    prompt = construct_llm_prompt(enriched, yaml.dump(windows_rule, sort_keys=False), yaml.dump(linux_rule, sort_keys=False), yara_text)
    llm_response = refine_with_llm(prompt, cfg)

    if not llm_response or "===YARA===" not in llm_response:
        return draft_sigma_yaml, yara_text

    sigma_part, yara_part = llm_response.split("===YARA===", 1)
    sigma_part, yara_part = sigma_part.strip(), yara_part.strip()

    if validate_sigma_yaml(sigma_part) and sigma_part:
        logger.info("LLM refinement accepted for %s.", enriched.base.cve_id)
        return sigma_part, yara_part

    logger.warning("LLM refinement for %s returned invalid YAML; keeping deterministic draft.", enriched.base.cve_id)
    return draft_sigma_yaml, yara_text


# -----------------------------------------------------------------------------
# Output writers
# -----------------------------------------------------------------------------
def write_rules(enriched: EnrichedVulnerability, sigma_yaml_text: str, yara_text: str, cfg: dict[str, Any]) -> dict[str, Path]:
    rules_dir = PROJECT_ROOT / cfg["engine"]["rules_output_dir"]
    rules_dir.mkdir(parents=True, exist_ok=True)

    sigma_path = rules_dir / f"{enriched.base.cve_id}_sigma.yml"
    yara_path = rules_dir / f"{enriched.base.cve_id}.yar"

    sigma_path.write_text(sigma_yaml_text, encoding="utf-8")
    yara_path.write_text(yara_text, encoding="utf-8")

    logger.info("Wrote %s and %s", sigma_path.name, yara_path.name)
    return {"sigma": sigma_path, "yara": yara_path}


@dataclass
class CoverageRow:
    cve_id: str
    vendor_product: str
    severity: str
    cvss_score: float | None
    category: str
    mitre_technique: str
    log_sources: str
    date_added: str
    known_ransomware_use: str


def update_dashboard(rows: list[CoverageRow], cfg: dict[str, Any]) -> Path:
    dashboard_path = PROJECT_ROOT / cfg["engine"]["dashboard_path"]

    header = (
        "# Active Threat Detection Coverage\n\n"
        f"_Auto-generated by `src/engine.py` — last updated "
        f"{RUN_TIMESTAMP.strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
        f"**{len(rows)} CVE(s) covered** across Windows Event Log / Sysmon, "
        "Linux auditd, and YARA detection surfaces.\n\n"
        "| CVE | Vendor / Product | Severity (CVSS) | Category | MITRE ATT&CK | "
        "Rule Types | Log Sources | Ransomware Use | Sigma | YARA |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )

    lines = [header]
    for row in rows:
        cvss_str = f"{row.severity.title()} ({row.cvss_score})" if row.cvss_score is not None else row.severity.title()
        lines.append(
            f"| {row.cve_id} | {row.vendor_product} | {cvss_str} | {row.category} | "
            f"{row.mitre_technique} | Sigma + YARA | {row.log_sources} | {row.known_ransomware_use} | "
            f"[`{row.cve_id}_sigma.yml`](rules/{row.cve_id}_sigma.yml) | "
            f"[`{row.cve_id}.yar`](rules/{row.cve_id}.yar) |\n"
        )

    dashboard_path.write_text("".join(lines), encoding="utf-8")
    logger.info("Updated dashboard at %s", dashboard_path)
    return dashboard_path


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------
def run_pipeline(limit: int | None, mock: bool, skip_nvd: bool, use_llm: bool) -> list[CoverageRow]:
    cfg = load_enrich_config()
    nvd_api_key = os.getenv(cfg["enrichment"]["nvd_api_key_env"])

    records = get_recent_vulnerabilities(limit=limit, mock=mock)
    rows: list[CoverageRow] = []

    for record in records:
        logger.info("Processing %s (%s %s)...", record.cve_id, record.vendor, record.product)
        enriched = enrich(record, cfg, nvd_api_key=nvd_api_key, skip_nvd=skip_nvd)

        windows_rule, linux_rule = build_sigma_rules(enriched)
        yara_text = build_yara_rule(enriched)
        draft_sigma_yaml = yaml.dump_all([windows_rule, linux_rule], sort_keys=False, default_flow_style=False)

        if use_llm:
            sigma_yaml_text, yara_text = _try_apply_llm_refinement(enriched, windows_rule, linux_rule, yara_text, cfg)
        else:
            sigma_yaml_text = draft_sigma_yaml

        if not validate_sigma_yaml(sigma_yaml_text):
            logger.warning("Falling back to deterministic draft for %s after validation failure.", record.cve_id)
            sigma_yaml_text = draft_sigma_yaml

        write_rules(enriched, sigma_yaml_text, yara_text, cfg)

        rows.append(CoverageRow(
            cve_id=record.cve_id,
            vendor_product=f"{record.vendor} / {record.product}",
            severity=enriched.cvss_severity,
            cvss_score=enriched.cvss_score,
            category=enriched.category,
            mitre_technique=enriched.mitre_technique,
            log_sources="Windows Event Log/Sysmon, Linux auditd",
            date_added=record.date_added,
            known_ransomware_use=record.known_ransomware_use,
        ))

    update_dashboard(rows, cfg)
    return rows


def _print_summary(rows: list[CoverageRow]) -> None:
    print("\n=== Detection Rule Generation Summary ===")
    for row in rows:
        print(f"  {row.cve_id:<18} [{row.severity:<8}] {row.category:<22} -> rules/{row.cve_id}_sigma.yml, rules/{row.cve_id}.yar")
    print(f"\n{len(rows)} CVE(s) processed. See DASHBOARD.md for the full coverage table.\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / "config" / ".env")
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Generate Sigma/YARA detection rules from live threat intel.")
    parser.add_argument("--limit", type=int, default=None, help="Number of recent KEV entries to process.")
    parser.add_argument("--mock", action="store_true", help="Use bundled offline sample data (no network calls).")
    parser.add_argument("--skip-nvd", action="store_true", help="Skip live NVD enrichment lookups.")
    parser.add_argument("--llm", action="store_true", help="Enable the optional LLM refinement pass.")
    args = parser.parse_args()

    use_llm_flag = args.llm or os.getenv("USE_LLM", "false").lower() == "true"
    summary_rows = run_pipeline(limit=args.limit, mock=args.mock, skip_nvd=args.skip_nvd, use_llm=use_llm_flag)
    _print_summary(summary_rows)
