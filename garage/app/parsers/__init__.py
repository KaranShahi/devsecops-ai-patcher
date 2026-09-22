from ..models import SupplierCode
from .base import InvoiceParser, ParsedInvoice, ParsedLine
from .eurocarparts import EurocarpartsParser
from .generic_csv import GenericCSVParser
from .gsf import GSFParser

PDF_PARSERS: dict[SupplierCode, type[InvoiceParser]] = {
    SupplierCode.gsf: GSFParser,
    SupplierCode.eurocarparts: EurocarpartsParser,
}


def get_parser(supplier_code: SupplierCode, filename: str) -> InvoiceParser:
    """Pick a parser by file extension first (CSV is the reliable path),
    then fall back to a supplier-specific PDF parser."""
    if filename.lower().endswith(".csv"):
        parser = GenericCSVParser()
        parser.supplier_code = supplier_code
        return parser
    parser_cls = PDF_PARSERS.get(supplier_code)
    if parser_cls is None:
        from .pdf_common import TableTextPDFParser

        return TableTextPDFParser()
    return parser_cls()


__all__ = [
    "InvoiceParser",
    "ParsedInvoice",
    "ParsedLine",
    "GSFParser",
    "EurocarpartsParser",
    "GenericCSVParser",
    "get_parser",
]
