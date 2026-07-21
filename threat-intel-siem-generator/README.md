# AI-Driven Threat Intel & SIEM Detection Rule Generator

**A SecOps automation pipeline that turns live vulnerability intelligence into deployable SIEM detection content — automatically.**

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Sigma](https://img.shields.io/badge/output-Sigma%20%2B%20YARA-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-active-brightgreen)

It ingests the [CISA Known Exploited Vulnerabilities (KEV)](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) catalog, enriches each CVE with CVSS/CWE data from the [NVD API](https://nvd.nist.gov/developers) and a MITRE ATT&CK-mapped detection-engineering knowledge base, then generates **syntactically valid Sigma rules** (Windows Event Log / Sysmon + Linux auditd) and **YARA rules** for every entry — ready to drop into Splunk, Sentinel, Elastic Security, or any Sigma-compatible SIEM.

---

## Why this exists

Every day, new CVEs land in the CISA KEV catalog with a hard remediation deadline — but writing a tuned, low-false-positive detection rule for each one is manual, slow, and requires a detection engineer's judgment about *where the exploitation actually shows up in telemetry*. This project automates that judgment call: it maps each vulnerability's weakness class (CWE) to the specific Windows Event IDs, Sysmon fields, and Linux auditd keys/syscalls that a real detection engineer would target, then renders that into spec-valid rule files — with an optional LLM refinement pass for further tightening.

## Architecture

```
                    ┌─────────────────┐
                    │   ingestor.py    │   CISA KEV live feed (cached fallback)
                    │                  │   or bundled --mock sample (offline demo)
                    └────────┬─────────┘
                             │ normalized VulnerabilityRecord
                             ▼
                    ┌─────────────────┐
                    │   enricher.py    │   NVD API → CVSS score, CWE list, references
                    │                  │   CWE/keyword → category, MITRE ATT&CK technique,
                    │                  │   Windows Event IDs, Linux auditd keys/syscalls
                    └────────┬─────────┘
                             │ EnrichedVulnerability
                             ▼
                    ┌─────────────────┐
                    │    engine.py     │   Deterministic Sigma + YARA templates
                    │ (Senior Detection│   (+ optional Claude refinement pass,
                    │  Engineer role)  │    always falls back to the valid draft)
                    └────────┬─────────┘
                             │
              ┌──────────────┼───────────────┐
              ▼              ▼               ▼
     rules/CVE-*_sigma.yml  rules/CVE-*.yar  DASHBOARD.md
     (Windows + Linux        (heuristic       (auto-generated coverage table)
      auditd, one file)       IOC matching)
```

## Features

- **Live threat ingestion** from the public CISA KEV feed, with automatic on-disk caching and graceful degradation to cached/mock data if the feed is unreachable — the pipeline never hard-fails on a network blip.
- **Grounded enrichment**, not fabrication. CVSS/CWE come from the real NVD API. Exploitation-vector guidance comes from a CWE → MITRE ATT&CK → telemetry mapping built on documented detection-engineering practice (e.g. `spoolsv.exe` spawning a child process is the canonical PrintNightmare indicator; `lsass.exe` spawning a child process is a critical-severity signal for SMB memory-corruption RCE).
- **Dual-platform Sigma output** — every CVE gets one multi-document YAML file with a Windows Event Log/Sysmon rule *and* a Linux auditd rule, each independently valid per the Sigma spec.
- **YARA rules with honest confidence levels.** Where a real, publicly documented static signature exists (e.g. the Log4Shell `${jndi:` prefix, SSRF cloud-metadata targets), the rule uses it. Where static matching is inherently unreliable (memory-corruption exploits), the rule says so explicitly in its `confidence` and `false_positive_notes` metadata instead of pretending otherwise.
- **False-positive-aware by design** — every rule ships with a `falsepositives` field describing the legitimate activity that could trigger it, so an analyst can tune before enabling blocking actions.
- **Optional LLM refinement pass** (Claude) that tightens the deterministic draft further. If no API key is configured, the package is missing, or the call fails for any reason, the pipeline **silently falls back to the validated deterministic draft** — the tool never depends on an external API to produce correct output.
- **Self-updating dashboard** — [`DASHBOARD.md`](DASHBOARD.md) is regenerated on every run with a full coverage table.
- **Fully offline demo mode** (`--mock`) using a bundled sample of real, well-known CVEs (Log4Shell, EternalBlue, PrintNightmare, ProxyLogon) — no network access or API keys required to see the whole pipeline work.

## Tech stack

Python 3.10+ · `requests` · `PyYAML` · `python-dotenv` · [Sigma](https://github.com/SigmaHQ/sigma) rule spec · [YARA](https://virustotal.github.io/yara/) · CISA KEV API · NVD 2.0 API · Anthropic Claude API (optional)

## Directory structure

```
threat-intel-siem-generator/
├── src/
│   ├── ingestor.py     # Threat feed ingestion (CISA KEV, live + cache + mock)
│   ├── enricher.py     # NVD enrichment + CWE/ATT&CK/telemetry classification
│   └── engine.py        # Sigma + YARA generation, LLM refinement, orchestration
├── rules/                # Generated output — CVE-XXXX-YYYY_sigma.yml, CVE-XXXX-YYYY.yar
├── config/
│   ├── settings.yaml    # Non-secret pipeline configuration
│   └── .env.example     # Template for API keys (copy to config/.env)
├── data/
│   ├── mock_kev_sample.json  # Bundled offline demo dataset
│   └── kev_cache.json        # Auto-generated live-feed cache (gitignored)
├── DASHBOARD.md          # Auto-generated "Active Threat Detection Coverage" table
├── requirements.txt
└── README.md
```

## Quickstart

```bash
cd threat-intel-siem-generator
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Run fully offline (no network, no API keys)

```bash
python src/engine.py --mock --skip-nvd
```

### Run against the live CISA KEV feed + live NVD enrichment

```bash
python src/engine.py --limit 5
```

### Enable the optional LLM refinement pass

```bash
cp config/.env.example config/.env
# edit config/.env and set ANTHROPIC_API_KEY, USE_LLM=true
python src/engine.py --limit 5 --llm
```

Every run writes `rules/<CVE-ID>_sigma.yml` and `rules/<CVE-ID>.yar` per vulnerability and regenerates [`DASHBOARD.md`](DASHBOARD.md).

## Sample output

`rules/CVE-2021-44228_sigma.yml` (Windows portion — Log4Shell, correctly targets `java.exe` spawning a shell):

```yaml
title: Possible Apache Log4j2 Remote Code Execution Vulnerability Exploitation Activity
id: 8e1ca3b0-1c26-5ea7-8bd0-fa92f05b9f8e
status: experimental
description: Detects process/logon telemetry consistent with exploitation of CVE-2021-44228
  (Apache Log4j2), classified as deserialization (MITRE ATT&CK T1190 - Exploit Public-Facing
  Application).
logsource:
  category: process_creation
  product: windows
detection:
  selection:
    ParentImage|endswith: [\java.exe]
    Image|endswith: [\cmd.exe, \powershell.exe, \pwsh.exe, \mshta.exe, \wscript.exe, \cscript.exe, \certutil.exe, \rundll32.exe]
  condition: selection
falsepositives:
- Legitimate administrative scripts launched by the parent process
level: high
```

`rules/CVE-2021-44228.yar` (JNDI exploitation string indicators):

```yara
rule CVE_2021_44228_deserialization
{
    meta:
        cve = "CVE-2021-44228"
        confidence = "medium"
    strings:
        $jndi_ldap = "${jndi:ldap://" ascii wide nocase
        $jndi_rmi  = "${jndi:rmi://" ascii wide nocase
        $jndi_dns  = "${jndi:dns://" ascii wide nocase
    condition:
        any of them
}
```

Both files are validated on every run: Sigma YAML is round-tripped through a YAML parser, and YARA output is checked with `yara-python` in CI-style verification during development.

## Active Threat Detection Coverage

See [`DASHBOARD.md`](DASHBOARD.md) for the live, auto-generated table (CVE, vendor/product, CVSS severity, detection category, MITRE ATT&CK technique, and links to every generated rule file). It's rewritten from scratch on every pipeline run — the file in this repo reflects the most recent `--mock` demo run.

## Methodology & responsible use

- Rules are generated from **deterministic, category-specific detection templates** grounded in documented exploitation patterns, not from fabricated payload signatures or invented file hashes.
- The optional LLM pass only *refines* an already-valid draft; it is never the sole source of a rule, and any invalid or unparseable response is discarded in favor of the deterministic version.
- **Every generated rule is a starting point, not a production-ready alert.** Validate against your own environment's baseline, tune the `falsepositives` exclusions, and have an analyst review before enabling automated blocking actions — this is standard practice for any detection content, hand-written or generated.

## Roadmap

- [ ] Shodan integration for internet-exposed-asset context alongside KEV data
- [ ] Splunk/Elastic direct-deploy connectors (push rather than file-drop)
- [ ] Sigma → platform-native query conversion via `sigma-cli` / pySigma backends
- [ ] Historical coverage trend view in the dashboard

## License

MIT
