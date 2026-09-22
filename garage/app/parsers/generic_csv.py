import csv
import io
from datetime import date, datetime

from .base import InvoiceParser, ParsedInvoice, ParsedLine

# Accepts a range of header spellings since suppliers name their export
# columns differently ("Part No" vs "Product Code" vs "SKU", etc).
COLUMN_ALIASES = {
    "part_number": {"part no", "part number", "product code", "sku", "code", "part"},
    "description": {"description", "product", "item", "name"},
    "quantity": {"qty", "quantity", "qty ordered", "units"},
    "unit_price": {"unit price", "price", "unit cost", "cost", "net price", "each"},
    "vat_rate": {"vat", "vat rate", "vat %", "tax rate"},
    "line_total": {"total", "line total", "net total", "amount"},
}

HEADER_ALIASES = {
    "invoice_number": {"invoice no", "invoice number", "invoice #", "inv no"},
    "invoice_date": {"invoice date", "date"},
}


def _normalize(header: str) -> str:
    return header.strip().lower()


def _map_columns(fieldnames: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    normalized = {_normalize(f): f for f in fieldnames}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[target] = normalized[alias]
                break
    return mapping


def _parse_number(raw: str | None, default: float = 0.0) -> float:
    if raw is None:
        return default
    cleaned = raw.strip().replace("£", "").replace(",", "")
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]
        try:
            return float(cleaned) / 100
        except ValueError:
            return default
    if not cleaned:
        return default
    try:
        return float(cleaned)
    except ValueError:
        return default


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


class GenericCSVParser(InvoiceParser):
    """Parses the CSV order/invoice exports most supplier trade portals offer.

    This is the recommended ingestion path: CSV exports have stable,
    labelled columns, unlike PDF layouts which shift between suppliers
    and even between invoice templates from the same supplier.
    """

    supplier_code = "other"

    def parse(self, file_path: str) -> ParsedInvoice:
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            text = f.read()

        result = ParsedInvoice(raw_text=text)
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            result.flag("CSV file has no header row - could not identify columns.")
            return result

        col_map = _map_columns(reader.fieldnames)
        normalized_headers = {_normalize(f): f for f in reader.fieldnames}

        for target, aliases in HEADER_ALIASES.items():
            for alias in aliases:
                if alias in normalized_headers:
                    col_map[f"_header_{target}"] = normalized_headers[alias]
                    break

        missing_required = [c for c in ("description", "unit_price") if c not in col_map]
        if missing_required:
            result.flag(
                "Could not find columns for: " + ", ".join(missing_required) + ". "
                f"Detected headers were: {reader.fieldnames}"
            )
            return result

        for row in reader:
            if "_header_invoice_number" in col_map and not result.invoice_number:
                result.invoice_number = row.get(col_map["_header_invoice_number"], "").strip() or None
            if "_header_invoice_date" in col_map and not result.invoice_date:
                result.invoice_date = _parse_date(row.get(col_map["_header_invoice_date"]))

            description = row.get(col_map.get("description", ""), "").strip()
            if not description:
                continue

            quantity = _parse_number(row.get(col_map.get("quantity", ""))) or 1.0
            unit_price = _parse_number(row.get(col_map.get("unit_price", "")))
            vat_rate = _parse_number(row.get(col_map.get("vat_rate", "")), default=0.20)
            if vat_rate > 1:  # e.g. "20" meaning 20%, not 0.20
                vat_rate = vat_rate / 100

            result.lines.append(
                ParsedLine(
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                    vat_rate=vat_rate,
                    part_number=(row.get(col_map.get("part_number", "")) or "").strip() or None,
                )
            )

        result.reconcile_totals()
        return result
