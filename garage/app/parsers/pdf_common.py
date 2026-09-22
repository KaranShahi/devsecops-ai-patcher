import re
from datetime import date, datetime

import pdfplumber

from .base import InvoiceParser, ParsedInvoice, ParsedLine

# Trade-counter invoice PDFs from suppliers like GSF and Eurocarparts commonly
# print one line item per row as: PART-CODE  DESCRIPTION  QTY  UNIT-PRICE  [VAT%]  LINE-TOTAL
# Real templates drift (columns can be reordered, spacing varies, some drop
# the VAT% column), so we try a small set of shapes and keep whichever
# matches most lines on the page.
LINE_PATTERNS = [
    re.compile(
        r"^(?P<part>[A-Z0-9][A-Z0-9\-/\.]{2,24})\s+(?P<desc>.+?)\s+"
        r"(?P<qty>\d+(?:\.\d+)?)\s+(?P<price>[\d,]+\.\d{2})\s+"
        r"(?:(?P<vat>\d{1,2})\s?%\s+)?(?P<total>[\d,]+\.\d{2})\s*$"
    ),
    re.compile(
        r"^(?P<part>[A-Z0-9][A-Z0-9\-/\.]{2,24})\s+(?P<desc>.+?)\s+"
        r"(?P<qty>\d+(?:\.\d+)?)\s*x\s*(?P<price>[\d,]+\.\d{2})\s+"
        r"(?P<total>[\d,]+\.\d{2})\s*$"
    ),
]

INVOICE_NUMBER_PATTERNS = [
    re.compile(r"invoice\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9\-/]+)", re.IGNORECASE),
]

DATE_PATTERNS = [
    re.compile(r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})"),
    re.compile(r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4})", re.IGNORECASE),
]

SUBTOTAL_PATTERNS = [re.compile(r"(?:sub\s*-?total|goods\s*total)\s*[:\-]?\s*£?\s*([\d,]+\.\d{2})", re.IGNORECASE)]
VAT_PATTERNS = [re.compile(r"\bvat\b\s*[:\-]?\s*£?\s*([\d,]+\.\d{2})", re.IGNORECASE)]
TOTAL_PATTERNS = [re.compile(r"(?:total\s*due|invoice\s*total|amount\s*due|grand\s*total)\s*[:\-]?\s*£?\s*([\d,]+\.\d{2})", re.IGNORECASE)]


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _parse_date(raw: str) -> date | None:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _search_first(patterns: list[re.Pattern], text: str) -> str | None:
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


class TableTextPDFParser(InvoiceParser):
    """Best-effort text/regex PDF parser shared by supplier-specific subclasses.

    Column layouts differ enough between suppliers (and between a supplier's
    own templates over time) that this can't guarantee a perfect parse.
    Every result is cross-checked against the invoice's own printed subtotal
    in `reconcile_totals`, and anything that doesn't reconcile - or where no
    lines were found at all - is flagged `needs_review` rather than posted
    silently. The generic CSV export from the supplier's portal is the more
    reliable ingestion path when it's available.
    """

    supplier_code = "other"

    def parse(self, file_path: str) -> ParsedInvoice:
        full_text_parts: list[str] = []
        candidate_lines: list[ParsedLine] = []

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                full_text_parts.append(text)
                for raw_line in text.splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    parsed = self._match_line(line)
                    if parsed:
                        candidate_lines.append(parsed)

        full_text = "\n".join(full_text_parts)
        result = ParsedInvoice(raw_text=full_text, lines=candidate_lines)

        inv_no = _search_first(INVOICE_NUMBER_PATTERNS, full_text)
        if inv_no:
            result.invoice_number = inv_no

        raw_date = _search_first(DATE_PATTERNS, full_text)
        if raw_date:
            result.invoice_date = _parse_date(raw_date)

        subtotal = _search_first(SUBTOTAL_PATTERNS, full_text)
        if subtotal:
            result.subtotal = _to_float(subtotal)
        vat = _search_first(VAT_PATTERNS, full_text)
        if vat:
            result.vat_amount = _to_float(vat)
        total = _search_first(TOTAL_PATTERNS, full_text)
        if total:
            result.total = _to_float(total)

        result.reconcile_totals()
        return result

    def _match_line(self, line: str) -> ParsedLine | None:
        for pattern in LINE_PATTERNS:
            m = pattern.match(line)
            if not m:
                continue
            groups = m.groupdict()
            vat_rate = 0.20
            if groups.get("vat"):
                vat_rate = float(groups["vat"]) / 100
            return ParsedLine(
                description=groups["desc"].strip(),
                quantity=_to_float(groups["qty"]),
                unit_price=_to_float(groups["price"]),
                vat_rate=vat_rate,
                part_number=groups["part"].strip(),
            )
        return None
