"""
ingestor.py — Threat Intelligence Ingestion Module

Pulls the public CISA Known Exploited Vulnerabilities (KEV) catalog and
normalizes it into a flat list of vulnerability records the rest of the
pipeline (enricher.py, engine.py) can consume.

Design notes:
  - The live feed requires no API key.
  - Every successful live fetch is cached to disk so the tool degrades
    gracefully to "last known good" data if the feed is unreachable.
  - A --mock mode ships with a small bundle of real, well-known CVEs
    (data/mock_kev_sample.json) so the whole pipeline can be demoed and
    graded with zero network access.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests
import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class VulnerabilityRecord:
    """Normalized representation of one KEV catalog entry."""

    cve_id: str
    vendor: str
    product: str
    vulnerability_name: str
    date_added: str
    short_description: str
    required_action: str
    due_date: str
    known_ransomware_use: str
    cwes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IngestorError(RuntimeError):
    """Raised when no vulnerability data could be obtained from any source."""


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load ingestion settings from config/settings.yaml."""
    config_path = config_path or PROJECT_ROOT / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _normalize_entry(raw: dict[str, Any]) -> VulnerabilityRecord:
    """Map a raw CISA KEV JSON object to our internal schema."""
    return VulnerabilityRecord(
        cve_id=raw.get("cveID", "UNKNOWN-CVE"),
        vendor=raw.get("vendorProject", "Unknown"),
        product=raw.get("product", "Unknown"),
        vulnerability_name=raw.get("vulnerabilityName", ""),
        date_added=raw.get("dateAdded", ""),
        short_description=raw.get("shortDescription", ""),
        required_action=raw.get("requiredAction", ""),
        due_date=raw.get("dueDate", ""),
        known_ransomware_use=raw.get("knownRansomwareCampaignUse", "Unknown"),
        cwes=raw.get("cwes", []) or [],
    )


def _fetch_live_catalog(cfg: dict[str, Any]) -> dict[str, Any]:
    """Fetch the live CISA KEV catalog with retries and exponential backoff."""
    url = cfg["ingestion"]["cisa_kev_url"]
    timeout = cfg["ingestion"]["request_timeout_seconds"]
    attempts = cfg["ingestion"]["retry_attempts"]
    backoff = cfg["ingestion"]["retry_backoff_seconds"]

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            logger.info("Fetching live CISA KEV catalog (attempt %d/%d)...", attempt, attempts)
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("KEV fetch attempt %d failed: %s", attempt, exc)
            if attempt < attempts:
                time.sleep(backoff * attempt)

    raise IngestorError(f"Failed to fetch live KEV catalog after {attempts} attempts: {last_error}")


def _write_cache(catalog: dict[str, Any], cfg: dict[str, Any]) -> None:
    cache_path = PROJECT_ROOT / cfg["ingestion"]["cache_path"]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, indent=2)
    logger.info("Cached live KEV catalog snapshot to %s", cache_path)


def _load_cache(cfg: dict[str, Any]) -> dict[str, Any] | None:
    cache_path = PROJECT_ROOT / cfg["ingestion"]["cache_path"]
    if not cache_path.exists():
        return None
    logger.info("Falling back to cached KEV snapshot at %s", cache_path)
    with open(cache_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_mock(cfg: dict[str, Any]) -> dict[str, Any]:
    mock_path = PROJECT_ROOT / cfg["ingestion"]["mock_data_path"]
    logger.info("Using bundled mock KEV sample at %s", mock_path)
    with open(mock_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def get_recent_vulnerabilities(
    limit: int | None = None,
    mock: bool = False,
    config_path: Path | None = None,
) -> list[VulnerabilityRecord]:
    """
    Return the `limit` most-recently-added vulnerabilities as normalized
    VulnerabilityRecord objects.

    Resolution order when mock=False: live feed -> local cache -> error.
    When mock=True, always use the bundled sample (no network call).
    """
    cfg = load_config(config_path)
    limit = limit or cfg["ingestion"]["default_limit"]

    if mock:
        catalog = _load_mock(cfg)
    else:
        try:
            catalog = _fetch_live_catalog(cfg)
            _write_cache(catalog, cfg)
        except IngestorError as exc:
            logger.error("Live ingestion failed: %s", exc)
            catalog = _load_cache(cfg)
            if catalog is None:
                raise IngestorError(
                    "No live feed, no cache, and mock mode disabled. "
                    "Re-run with --mock to use bundled sample data."
                ) from exc

    entries = catalog.get("vulnerabilities", [])
    # KEV catalog is already ordered oldest->newest; take the most recent N.
    recent_raw = entries[-limit:] if limit else entries
    records = [_normalize_entry(raw) for raw in reversed(recent_raw)]
    logger.info("Ingested %d vulnerability record(s).", len(records))
    return records


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Fetch recent CISA KEV entries.")
    parser.add_argument("--limit", type=int, default=None, help="Number of entries to fetch.")
    parser.add_argument("--mock", action="store_true", help="Use bundled offline sample data.")
    args = parser.parse_args()

    for record in get_recent_vulnerabilities(limit=args.limit, mock=args.mock):
        print(json.dumps(record.to_dict(), indent=2))
