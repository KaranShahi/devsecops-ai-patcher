from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from ..database import get_session
from ..models import CustomerInvoice, InvoiceStatus
from ..pdf import render_invoice_pdf
from ..templating import templates
from .. import xero_client

router = APIRouter(prefix="/customer-invoices", tags=["customer-invoices"])


@router.get("")
def list_invoices(request: Request, session: Session = Depends(get_session)):
    invoices = session.exec(select(CustomerInvoice).order_by(CustomerInvoice.invoice_date.desc())).all()
    return templates.TemplateResponse(request, "customer_invoices.html", {"invoices": invoices})


@router.get("/{invoice_id}")
def invoice_detail(invoice_id: int, request: Request, session: Session = Depends(get_session)):
    invoice = session.get(CustomerInvoice, invoice_id)
    if invoice is None:
        return RedirectResponse("/customer-invoices", status_code=303)
    xero_connected = session.exec(select(xero_client.XeroToken)).first() is not None
    return templates.TemplateResponse(
        request,
        "customer_invoice_detail.html",
        {
            "invoice": invoice,
            "xero_connected": xero_connected,
            "xero_error": request.query_params.get("xero_error"),
        },
    )


@router.get("/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, session: Session = Depends(get_session)):
    invoice = session.get(CustomerInvoice, invoice_id)
    if invoice is None:
        return RedirectResponse("/customer-invoices", status_code=303)
    pdf_bytes = render_invoice_pdf(invoice)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{invoice.invoice_number}.pdf"'},
    )


@router.post("/{invoice_id}/status/{new_status}")
def update_status(invoice_id: int, new_status: InvoiceStatus, session: Session = Depends(get_session)):
    invoice = session.get(CustomerInvoice, invoice_id)
    if invoice:
        invoice.status = new_status
        session.add(invoice)
        session.commit()
    return RedirectResponse(f"/customer-invoices/{invoice_id}", status_code=303)


@router.post("/{invoice_id}/sync-to-xero")
def sync_to_xero(invoice_id: int, session: Session = Depends(get_session)):
    invoice = session.get(CustomerInvoice, invoice_id)
    if invoice is None:
        return RedirectResponse("/customer-invoices", status_code=303)
    try:
        xero_client.push_customer_invoice(session, invoice)
    except xero_client.XeroError as exc:
        return RedirectResponse(
            f"/customer-invoices/{invoice_id}?xero_error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(f"/customer-invoices/{invoice_id}", status_code=303)
