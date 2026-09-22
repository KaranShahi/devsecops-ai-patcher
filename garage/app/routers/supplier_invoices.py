import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..config import settings
from ..database import get_session
from ..models import Supplier, SupplierCode, SupplierInvoice, SupplierInvoiceLine
from ..parsers import get_parser
from ..templating import templates

router = APIRouter(prefix="/supplier-invoices", tags=["supplier-invoices"])


def _ensure_supplier(session: Session, code: SupplierCode) -> Supplier:
    supplier = session.exec(select(Supplier).where(Supplier.code == code)).first()
    if supplier:
        return supplier
    names = {SupplierCode.gsf: "GSF Car Parts", SupplierCode.eurocarparts: "Eurocarparts", SupplierCode.other: "Other"}
    supplier = Supplier(name=names.get(code, code.value), code=code)
    session.add(supplier)
    session.commit()
    session.refresh(supplier)
    return supplier


@router.get("")
def list_supplier_invoices(request: Request, session: Session = Depends(get_session)):
    invoices = session.exec(select(SupplierInvoice).order_by(SupplierInvoice.imported_at.desc())).all()
    return templates.TemplateResponse(
        request, "supplier_invoices.html", {"invoices": invoices, "suppliers": list(SupplierCode)}
    )


@router.post("/upload")
async def upload_supplier_invoice(
    supplier_code: SupplierCode = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    supplier = _ensure_supplier(session, supplier_code)

    suffix = Path(file.filename).suffix or ""
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = settings.upload_dir / stored_name
    with stored_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    parser = get_parser(supplier_code, file.filename)
    parsed = parser.parse(str(stored_path))

    invoice = SupplierInvoice(
        supplier_id=supplier.id,
        invoice_number=parsed.invoice_number,
        invoice_date=parsed.invoice_date,
        subtotal=parsed.subtotal or 0.0,
        vat_amount=parsed.vat_amount or 0.0,
        total=parsed.total or 0.0,
        currency="GBP",
        source_filename=file.filename,
        stored_path=str(stored_path),
        raw_text=parsed.raw_text[:20000],
        needs_review=parsed.needs_review,
        parser_notes="; ".join(parsed.notes) or None,
    )
    session.add(invoice)
    session.commit()
    session.refresh(invoice)

    for i, line in enumerate(parsed.lines, start=1):
        session.add(
            SupplierInvoiceLine(
                supplier_invoice_id=invoice.id,
                line_no=i,
                part_number=line.part_number,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                vat_rate=line.vat_rate,
                line_total=line.line_total,
            )
        )
    session.commit()

    return RedirectResponse(f"/supplier-invoices/{invoice.id}", status_code=303)


@router.get("/{invoice_id}")
def supplier_invoice_detail(invoice_id: int, request: Request, session: Session = Depends(get_session)):
    invoice = session.get(SupplierInvoice, invoice_id)
    if invoice is None:
        return RedirectResponse("/supplier-invoices", status_code=303)
    return templates.TemplateResponse(request, "supplier_invoice_detail.html", {"invoice": invoice})
