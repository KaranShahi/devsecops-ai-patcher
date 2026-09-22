from .pdf_common import TableTextPDFParser


class EurocarpartsParser(TableTextPDFParser):
    """PDF parser tuned for Eurocarparts (ECP) trade invoices.

    Shares the generic table-row parsing with GSFParser; kept separate so
    supplier-specific fixes (header wording, column order) can diverge
    without affecting the other supplier.
    """

    supplier_code = "eurocarparts"
