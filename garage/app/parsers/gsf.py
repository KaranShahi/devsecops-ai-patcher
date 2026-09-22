from .pdf_common import TableTextPDFParser


class GSFParser(TableTextPDFParser):
    """PDF parser tuned for GSF Car Parts trade invoices.

    Column layout is the shared PART / DESCRIPTION / QTY / PRICE / VAT% /
    TOTAL shape handled by TableTextPDFParser - kept as its own class so
    GSF-specific header quirks can be added here without touching the
    Eurocarparts parser.
    """

    supplier_code = "gsf"
