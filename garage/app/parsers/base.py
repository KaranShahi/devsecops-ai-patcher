from dataclasses import dataclass, field
from datetime import date


@dataclass
class ParsedLine:
    description: str
    quantity: float = 1.0
    unit_price: float = 0.0
    vat_rate: float = 0.20
    part_number: str | None = None

    @property
    def line_total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass
class ParsedInvoice:
    invoice_number: str | None = None
    invoice_date: date | None = None
    lines: list[ParsedLine] = field(default_factory=list)
    subtotal: float | None = None
    vat_amount: float | None = None
    total: float | None = None
    needs_review: bool = False
    notes: list[str] = field(default_factory=list)
    raw_text: str = ""

    def computed_subtotal(self) -> float:
        return round(sum(l.line_total for l in self.lines), 2)

    def computed_vat(self) -> float:
        return round(sum(l.line_total * l.vat_rate for l in self.lines), 2)

    def flag(self, note: str) -> None:
        self.needs_review = True
        self.notes.append(note)

    def reconcile_totals(self) -> None:
        """Cross-check extracted totals against the sum of parsed lines.

        Supplier PDFs vary enough in layout that a mis-parsed column is
        common; comparing against the invoice's own printed total is the
        cheapest way to catch it, so a bad parse is flagged for a human
        rather than silently posted.
        """
        computed = self.computed_subtotal()
        if self.subtotal is None:
            self.subtotal = computed
        elif abs(self.subtotal - computed) > 0.05:
            self.flag(
                f"Parsed line items sum to {computed:.2f} but the invoice "
                f"prints a subtotal of {self.subtotal:.2f} - check the parse."
            )
        if self.vat_amount is None:
            self.vat_amount = self.computed_vat()
        if self.total is None:
            self.total = round((self.subtotal or 0) + (self.vat_amount or 0), 2)
        if not self.lines:
            self.flag("No line items could be extracted from this file.")


class InvoiceParser:
    supplier_code = "other"

    def parse(self, file_path: str) -> ParsedInvoice:
        raise NotImplementedError
