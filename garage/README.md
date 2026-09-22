# Garage Manager

A small web app for a car garage: track customers, vehicles and jobs,
import supplier part invoices (GSF Car Parts, Eurocarparts), cost jobs
with a parts markup, raise customer invoices with VAT, and push those
invoices into Xero.

## Stack

FastAPI + SQLModel (SQLite by default) + server-rendered Jinja2 templates.
No JS build step - a little vanilla JS handles the customer→vehicle
dropdown on the "new job" form.

## Setup

```bash
cd garage
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit company details / Xero credentials
python run.py
```

Open http://localhost:8000.

## How it fits together

```
Customer ──< Vehicle ──< Job ──< JobLaborLine
                           └───< JobPartLine ──> SupplierInvoiceLine ──< SupplierInvoice ──> Supplier
                           └───> CustomerInvoice ──< CustomerInvoiceLine
                                       │
                                       └──> pushed to Xero as an ACCREC invoice
```

1. **Import a supplier invoice** (Supplier Invoices → upload a GSF or
   Eurocarparts export). CSV exports from the supplier's trade portal are
   the reliable path - stable, named columns. PDF invoices are parsed on
   a best-effort basis (`app/parsers/pdf_common.py`) using regex over the
   extracted text; because layouts vary between suppliers and even
   between a supplier's own templates, every parse is cross-checked
   against the invoice's own printed subtotal and flagged `needs_review`
   when it doesn't reconcile, or when no lines could be extracted at all.
   Check the raw extracted text on the invoice's detail page if a PDF
   parse needs fixing up.
2. **Raise a job** against a customer's vehicle, log labour hours and
   allocate parts to it - either straight from an imported supplier
   invoice line (cost price comes from the supplier invoice, you set the
   markup %) or entered by hand.
3. **Generate the customer invoice** from the job. VAT is calculated per
   line (`app/invoicing.py`); the job is marked `invoiced` and a
   sequential invoice number is issued.
4. **Download the PDF** or **push it to Xero**. Xero sync
   (`app/xero_client.py`) uses a real OAuth2 authorization-code flow
   against Xero's Accounting API - it creates/reuses a Xero Contact for
   the customer and posts an ACCREC invoice with the same line items,
   quantities and VAT (`OUTPUT2`, Xero's UK 20% standard rate code).

## Xero setup

1. Create an app at https://developer.xero.com/app/manage with the
   scopes `openid profile email accounting.contacts accounting.transactions offline_access`
   and a redirect URI matching `XERO_REDIRECT_URI` in your `.env`
   (default `http://localhost:8000/xero/callback`).
2. Set `XERO_CLIENT_ID` and `XERO_CLIENT_SECRET` in `.env` and restart.
3. Visit **Xero** in the nav and click **Connect to Xero**.

This talks directly to Xero's HTTP API with the garage's own registered
app credentials - it does not use any Xero MCP tool, since the ones
available in this environment are read-only reporting tools that can't
create contacts or invoices.

## Tests

```bash
pytest tests/ -v
```

Covers the VAT/total math in `invoicing.py`, the CSV and PDF-text
parsers (including the review-flagging on a bad or incomplete parse),
and a full customer → vehicle → job → invoice flow through the API.

## Notes / things to tune for a real garage

- `DEFAULT_VAT_RATE`, `DEFAULT_PARTS_MARKUP_PCT` and `DEFAULT_LABOR_RATE`
  in `.env` are starting points - every line's VAT rate and a part's
  markup % can be overridden per line.
- The PDF parsers' regex patterns are based on the common
  PART / DESCRIPTION / QTY / PRICE / VAT% / TOTAL row shape used by most
  UK trade-counter invoices; if a specific GSF or Eurocarparts template
  doesn't match, tune the patterns in `app/parsers/pdf_common.py` against
  the raw extracted text shown on that invoice's detail page, or prefer
  the CSV export where the portal offers one.
- Account codes on invoice lines (`200` for labour, `310` for parts in
  `invoicing.py`) are placeholders - change them to match your Xero
  chart of accounts.
