"""
enricher.py — Threat Intelligence Enrichment Module

Takes a normalized VulnerabilityRecord from ingestor.py and enriches it with:
  - CVSS score/vector and CWE descriptions pulled from the public NVD API
  - A detection category (e.g. remote_code_execution, auth_bypass,
    privilege_escalation, deserialization, ssrf, path_traversal) derived
    from CWE IDs and keyword analysis of the vulnerability name/description
  - A MITRE ATT&CK technique mapping
  - Concrete log source guidance: which Windows Event IDs / Sysmon event
    types and which Linux auditd keys/syscalls are relevant
  - False-positive guidance the detection engine uses to scope rules tightly

This is deliberately *not* a fabricated payload/hash intelligence feed —
there is no reliable free public source for real malware hashes tied to a
given CVE. Instead this module applies grounded detection-engineering
knowledge (CWE taxonomy -> observable telemetry) to tell the rule generator
*where to look*, which is what actually drives Sigma/YARA rule quality.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml

from ingestor import VulnerabilityRecord

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# -----------------------------------------------------------------------------
# CWE -> detection category / ATT&CK technique / telemetry mapping
# -----------------------------------------------------------------------------
# Each entry describes where a given weakness class actually shows up in
# Windows Event Logs and Linux auditd, so generated rules target real,
# available telemetry instead of guessing.
CWE_DETECTION_MAP: dict[str, dict[str, Any]] = {
    "CWE-78": {  # OS Command Injection
        "category": "command_execution",
        "mitre_technique": "T1059",
        "mitre_name": "Command and Scripting Interpreter",
        "windows_event_ids": ["4688", "Sysmon-1"],
        "windows_fields": ["NewProcessName", "CommandLine", "ParentProcessName"],
        "auditd_keys": ["execve", "exec_shell"],
        "auditd_syscalls": ["execve", "execveat"],
    },
    "CWE-94": {  # Code Injection
        "category": "command_execution",
        "mitre_technique": "T1059",
        "mitre_name": "Command and Scripting Interpreter",
        "windows_event_ids": ["4688", "Sysmon-1", "Sysmon-7"],
        "windows_fields": ["CommandLine", "Image", "ImageLoaded"],
        "auditd_keys": ["execve", "code_injection"],
        "auditd_syscalls": ["execve", "ptrace"],
    },
    "CWE-502": {  # Deserialization of Untrusted Data
        "category": "deserialization",
        "mitre_technique": "T1190",
        "mitre_name": "Exploit Public-Facing Application",
        "windows_event_ids": ["4688", "Sysmon-1", "Sysmon-3", "Sysmon-22"],
        "windows_fields": ["CommandLine", "ParentImage", "DestinationHostname"],
        "auditd_keys": ["execve", "network_connect"],
        "auditd_syscalls": ["execve", "connect"],
    },
    "CWE-918": {  # Server-Side Request Forgery
        "category": "ssrf",
        "mitre_technique": "T1190",
        "mitre_name": "Exploit Public-Facing Application",
        "windows_event_ids": ["Sysmon-3", "Sysmon-22"],
        "windows_fields": ["DestinationIp", "DestinationPort", "QueryName"],
        "auditd_keys": ["network_connect"],
        "auditd_syscalls": ["connect", "sendto"],
    },
    "CWE-269": {  # Improper Privilege Management
        "category": "privilege_escalation",
        "mitre_technique": "T1068",
        "mitre_name": "Exploitation for Privilege Escalation",
        "windows_event_ids": ["4672", "4673", "4688"],
        "windows_fields": ["SubjectUserName", "PrivilegeList", "NewProcessName"],
        "auditd_keys": ["privilege_escalation", "setuid"],
        "auditd_syscalls": ["setuid", "setgid", "capset"],
    },
    "CWE-427": {  # Uncontrolled Search Path Element (DLL hijack / spooler abuse)
        "category": "privilege_escalation",
        "mitre_technique": "T1574",
        "mitre_name": "Hijack Execution Flow",
        "windows_event_ids": ["Sysmon-7", "Sysmon-11", "4688"],
        "windows_fields": ["ImageLoaded", "TargetFilename", "Image"],
        "auditd_keys": ["file_write", "privilege_escalation"],
        "auditd_syscalls": ["open", "openat", "write"],
    },
    "CWE-287": {  # Improper Authentication
        "category": "auth_bypass",
        "mitre_technique": "T1078",
        "mitre_name": "Valid Accounts",
        "windows_event_ids": ["4624", "4625", "4648"],
        "windows_fields": ["TargetUserName", "LogonType", "IpAddress"],
        "auditd_keys": ["auth_bypass", "logon"],
        "auditd_syscalls": [],
    },
    "CWE-306": {  # Missing Authentication for Critical Function
        "category": "auth_bypass",
        "mitre_technique": "T1078",
        "mitre_name": "Valid Accounts",
        "windows_event_ids": ["4624", "4648"],
        "windows_fields": ["TargetUserName", "LogonType"],
        "auditd_keys": ["auth_bypass"],
        "auditd_syscalls": [],
    },
    "CWE-798": {  # Use of Hard-coded Credentials
        "category": "credential_access",
        "mitre_technique": "T1552",
        "mitre_name": "Unsecured Credentials",
        "windows_event_ids": ["4624", "4648"],
        "windows_fields": ["TargetUserName", "IpAddress"],
        "auditd_keys": ["credential_access"],
        "auditd_syscalls": ["open", "openat"],
    },
    "CWE-22": {  # Path Traversal
        "category": "path_traversal",
        "mitre_technique": "T1083",
        "mitre_name": "File and Directory Discovery",
        "windows_event_ids": ["4663", "Sysmon-11"],
        "windows_fields": ["ObjectName", "TargetFilename"],
        "auditd_keys": ["path_traversal", "file_access"],
        "auditd_syscalls": ["open", "openat", "stat"],
    },
    "CWE-119": {  # Buffer Overflow / memory corruption (e.g. EternalBlue)
        "category": "memory_corruption_rce",
        "mitre_technique": "T1210",
        "mitre_name": "Exploitation of Remote Services",
        "windows_event_ids": ["Sysmon-1", "Sysmon-3", "5156"],
        "windows_fields": ["CommandLine", "DestinationPort", "Image"],
        "auditd_keys": ["network_connect", "execve"],
        "auditd_syscalls": ["connect", "execve"],
    },
    "CWE-787": {  # Out-of-bounds Write
        "category": "memory_corruption_rce",
        "mitre_technique": "T1210",
        "mitre_name": "Exploitation of Remote Services",
        "windows_event_ids": ["Sysmon-1", "Sysmon-3", "5156"],
        "windows_fields": ["CommandLine", "DestinationPort"],
        "auditd_keys": ["network_connect", "execve"],
        "auditd_syscalls": ["connect", "execve"],
    },
    "CWE-400": {  # Uncontrolled Resource Consumption
        "category": "resource_exhaustion",
        "mitre_technique": "T1499",
        "mitre_name": "Endpoint Denial of Service",
        "windows_event_ids": ["Sysmon-1"],
        "windows_fields": ["CommandLine"],
        "auditd_keys": ["resource_exhaustion"],
        "auditd_syscalls": [],
    },
    "CWE-20": {  # Improper Input Validation (generic fallback for many web CVEs)
        "category": "input_validation",
        "mitre_technique": "T1190",
        "mitre_name": "Exploit Public-Facing Application",
        "windows_event_ids": ["Sysmon-1", "Sysmon-3"],
        "windows_fields": ["CommandLine", "DestinationIp"],
        "auditd_keys": ["execve", "network_connect"],
        "auditd_syscalls": ["execve", "connect"],
    },
}

# Fallback used when no known CWE maps, keyed by keyword found in the
# vulnerability name / description (checked in order, first match wins).
KEYWORD_FALLBACK_MAP: list[tuple[str, str]] = [
    ("remote code execution", "CWE-94"),
    ("rce", "CWE-94"),
    ("privilege escalation", "CWE-269"),
    ("elevation of privilege", "CWE-269"),
    ("authentication bypass", "CWE-287"),
    ("auth bypass", "CWE-287"),
    ("deserialization", "CWE-502"),
    ("server-side request forgery", "CWE-918"),
    ("ssrf", "CWE-918"),
    ("path traversal", "CWE-22"),
    ("directory traversal", "CWE-22"),
    ("buffer overflow", "CWE-119"),
    ("memory corruption", "CWE-787"),
    ("denial of service", "CWE-400"),
]

DEFAULT_CWE = "CWE-20"  # generic "improper input validation" fallback


@dataclass
class EnrichedVulnerability:
    """A VulnerabilityRecord plus derived detection-engineering context."""

    base: VulnerabilityRecord
    cvss_score: float | None
    cvss_severity: str
    cwe_descriptions: dict[str, str]
    category: str
    mitre_technique: str
    mitre_name: str
    windows_event_ids: list[str]
    windows_fields: list[str]
    auditd_keys: list[str]
    auditd_syscalls: list[str]
    references: list[str] = field(default_factory=list)
    nvd_lookup_succeeded: bool = False


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    config_path = config_path or PROJECT_ROOT / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _cvss_severity(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low"


def fetch_nvd_details(
    cve_id: str,
    cfg: dict[str, Any],
    api_key: str | None = None,
) -> dict[str, Any] | None:
    """
    Look up CVSS score/vector, CWE list, and references for a CVE via the
    public NVD 2.0 REST API. Returns None (rather than raising) on any
    network/parse failure so the pipeline can continue with KEV-only data —
    NVD enrichment is a nice-to-have, not a hard dependency.
    """
    url = cfg["enrichment"]["nvd_api_url"]
    timeout = cfg["enrichment"]["request_timeout_seconds"]
    attempts = cfg["enrichment"]["retry_attempts"]
    backoff = cfg["enrichment"]["retry_backoff_seconds"]
    headers = {"apiKey": api_key} if api_key else {}

    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url, params={"cveId": cve_id}, headers=headers, timeout=timeout
            )
            if response.status_code == 403 or response.status_code == 429:
                logger.warning("NVD rate-limited us for %s (HTTP %d).", cve_id, response.status_code)
                time.sleep(backoff * attempt)
                continue
            response.raise_for_status()
            data = response.json()
            vulnerabilities = data.get("vulnerabilities", [])
            if not vulnerabilities:
                logger.warning("NVD returned no records for %s.", cve_id)
                return None
            return vulnerabilities[0]["cve"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            logger.warning("NVD lookup attempt %d for %s failed: %s", attempt, cve_id, exc)
            if attempt < attempts:
                time.sleep(backoff * attempt)

    logger.error("NVD enrichment failed for %s after %d attempts; continuing without it.", cve_id, attempts)
    return None


def _extract_cvss(nvd_cve: dict[str, Any]) -> tuple[float | None, str]:
    metrics = nvd_cve.get("metrics", {})
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        if key in metrics and metrics[key]:
            cvss_data = metrics[key][0]["cvssData"]
            return cvss_data.get("baseScore"), cvss_data.get("vectorString", "")
    return None, ""


def _extract_cwes(nvd_cve: dict[str, Any]) -> list[str]:
    cwes = []
    for weakness in nvd_cve.get("weaknesses", []):
        for desc in weakness.get("description", []):
            value = desc.get("value", "")
            if value.startswith("CWE-"):
                cwes.append(value)
    return cwes


def _extract_references(nvd_cve: dict[str, Any], limit: int = 5) -> list[str]:
    return [ref.get("url", "") for ref in nvd_cve.get("references", [])[:limit]]


def classify_category(record: VulnerabilityRecord, resolved_cwes: list[str]) -> str:
    """Pick the strongest-matching CWE id to drive category/telemetry mapping."""
    for cwe in resolved_cwes:
        if cwe in CWE_DETECTION_MAP:
            return cwe

    haystack = f"{record.vulnerability_name} {record.short_description}".lower()
    for keyword, cwe in KEYWORD_FALLBACK_MAP:
        if keyword in haystack:
            return cwe

    return DEFAULT_CWE


def enrich(
    record: VulnerabilityRecord,
    cfg: dict[str, Any] | None = None,
    nvd_api_key: str | None = None,
    skip_nvd: bool = False,
) -> EnrichedVulnerability:
    """Full enrichment pipeline for a single vulnerability record."""
    cfg = cfg or load_config()

    nvd_cve = None if skip_nvd else fetch_nvd_details(record.cve_id, cfg, nvd_api_key)

    if nvd_cve:
        cvss_score, _vector = _extract_cvss(nvd_cve)
        nvd_cwes = _extract_cwes(nvd_cve)
        references = _extract_references(nvd_cve)
        nvd_lookup_succeeded = True
    else:
        cvss_score = None
        nvd_cwes = []
        references = []
        nvd_lookup_succeeded = False

    resolved_cwes = nvd_cwes or record.cwes
    matched_cwe = classify_category(record, resolved_cwes)
    detection_ctx = CWE_DETECTION_MAP.get(matched_cwe, CWE_DETECTION_MAP[DEFAULT_CWE])

    return EnrichedVulnerability(
        base=record,
        cvss_score=cvss_score,
        cvss_severity=_cvss_severity(cvss_score),
        cwe_descriptions={cwe: "" for cwe in (resolved_cwes or [matched_cwe])},
        category=detection_ctx["category"],
        mitre_technique=detection_ctx["mitre_technique"],
        mitre_name=detection_ctx["mitre_name"],
        windows_event_ids=detection_ctx["windows_event_ids"],
        windows_fields=detection_ctx["windows_fields"],
        auditd_keys=detection_ctx["auditd_keys"],
        auditd_syscalls=detection_ctx["auditd_syscalls"],
        references=references,
        nvd_lookup_succeeded=nvd_lookup_succeeded,
    )


if __name__ == "__main__":
    import argparse
    import json
    import os

    from ingestor import get_recent_vulnerabilities

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Enrich recent CISA KEV entries.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--skip-nvd", action="store_true", help="Skip live NVD lookups.")
    args = parser.parse_args()

    config = load_config()
    api_key = os.getenv(config["enrichment"]["nvd_api_key_env"])

    for rec in get_recent_vulnerabilities(limit=args.limit, mock=args.mock):
        enriched = enrich(rec, config, nvd_api_key=api_key, skip_nvd=args.skip_nvd)
        print(json.dumps({
            "cve_id": enriched.base.cve_id,
            "category": enriched.category,
            "cvss_score": enriched.cvss_score,
            "cvss_severity": enriched.cvss_severity,
            "mitre_technique": enriched.mitre_technique,
            "windows_event_ids": enriched.windows_event_ids,
            "auditd_keys": enriched.auditd_keys,
            "nvd_lookup_succeeded": enriched.nvd_lookup_succeeded,
        }, indent=2))
