import io
import textwrap

from app.parsers.generic_csv import GenericCSVParser
from app.parsers.pdf_common import TableTextPDFParser


def write_csv(tmp_path, content):
    path = tmp_path / "invoice.csv"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_generic_csv_parser_happy_path(tmp_path):
    csv_content = textwrap.dedent(
        """\
        Invoice Number,Invoice Date,Part No,Description,Qty,Unit Price,VAT
        INV-1001,15/03/2024,BP-100,Front Brake Pads,2,24.50,20
        INV-1001,15/03/2024,OF-220,Oil Filter,1,8.99,20
        """
    )
    path = write_csv(tmp_path, csv_content)

    parsed = GenericCSVParser().parse(path)

    assert parsed.invoice_number == "INV-1001"
    assert parsed.invoice_date.isoformat() == "2024-03-15"
    assert len(parsed.lines) == 2
    assert parsed.lines[0].part_number == "BP-100"
    assert parsed.lines[0].quantity == 2
    assert parsed.lines[0].unit_price == 24.50
    assert parsed.lines[0].vat_rate == 0.20
    expected_subtotal = round(2 * 24.50 + 1 * 8.99, 2)
    assert parsed.subtotal == expected_subtotal
    assert parsed.needs_review is False


def test_generic_csv_parser_flags_missing_columns(tmp_path):
    path = write_csv(tmp_path, "Foo,Bar\n1,2\n")
    parsed = GenericCSVParser().parse(path)
    assert parsed.needs_review is True
    assert parsed.lines == []


def test_generic_csv_parser_flags_no_data_rows(tmp_path):
    path = write_csv(tmp_path, "Description,Unit Price\n")
    parsed = GenericCSVParser().parse(path)
    assert parsed.needs_review is True


def test_table_text_pdf_parser_matches_typical_line():
    parser = TableTextPDFParser()
    line = parser._match_line("BP-100 Front Brake Pads 2 24.50 20% 49.00")
    assert line is not None
    assert line.part_number == "BP-100"
    assert line.description == "Front Brake Pads"
    assert line.quantity == 2
    assert line.unit_price == 24.50
    assert line.vat_rate == 0.20
    assert line.line_total == 49.00


def test_table_text_pdf_parser_ignores_non_matching_lines():
    parser = TableTextPDFParser()
    assert parser._match_line("Thank you for your business") is None
    assert parser._match_line("Page 1 of 2") is None


def test_reconcile_totals_flags_mismatch():
    from app.parsers.base import ParsedInvoice, ParsedLine

    parsed = ParsedInvoice(lines=[ParsedLine(description="Widget", quantity=1, unit_price=10.0)])
    parsed.subtotal = 999.0  # printed total doesn't match the one line item
    parsed.reconcile_totals()
    assert parsed.needs_review is True
    assert any("check the parse" in note for note in parsed.notes)


def test_reconcile_totals_accepts_matching_totals():
    from app.parsers.base import ParsedInvoice, ParsedLine

    parsed = ParsedInvoice(lines=[ParsedLine(description="Widget", quantity=2, unit_price=10.0)])
    parsed.subtotal = 20.0
    parsed.reconcile_totals()
    assert parsed.needs_review is False
