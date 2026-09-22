from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..config import settings
from ..database import get_session
from ..invoicing import apply_markup, generate_customer_invoice
from ..models import Customer, Job, JobLaborLine, JobPartLine, JobStatus, SupplierInvoiceLine, Vehicle
from ..templating import templates

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(request: Request, status: str | None = None, session: Session = Depends(get_session)):
    query = select(Job).order_by(Job.created_at.desc())
    if status:
        query = query.where(Job.status == status)
    jobs = session.exec(query).all()
    return templates.TemplateResponse(
        request, "jobs.html", {"jobs": jobs, "statuses": list(JobStatus), "active_status": status}
    )


@router.get("/new")
def new_job_form(request: Request, session: Session = Depends(get_session)):
    customers = session.exec(select(Customer).order_by(Customer.name)).all()
    return templates.TemplateResponse(request, "job_new.html", {"customers": customers})


@router.post("")
def create_job(
    customer_id: int = Form(...),
    vehicle_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    session: Session = Depends(get_session),
):
    job = Job(customer_id=customer_id, vehicle_id=vehicle_id, title=title, description=description or None)
    session.add(job)
    session.commit()
    session.refresh(job)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@router.get("/{job_id}")
def job_detail(job_id: int, request: Request, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        return RedirectResponse("/jobs", status_code=303)
    unallocated_lines = session.exec(
        select(SupplierInvoiceLine).where(SupplierInvoiceLine.allocated_job_id.is_(None))
    ).all()
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": job,
            "unallocated_lines": unallocated_lines,
            "default_labor_rate": settings.default_labor_rate,
            "default_markup_pct": settings.default_parts_markup_pct,
            "default_vat_rate": settings.default_vat_rate,
        },
    )


@router.post("/{job_id}/labor")
def add_labor_line(
    job_id: int,
    description: str = Form(...),
    hours: float = Form(...),
    rate: float = Form(...),
    session: Session = Depends(get_session),
):
    line = JobLaborLine(job_id=job_id, description=description, hours=hours, rate=rate)
    session.add(line)
    job = session.get(Job, job_id)
    if job and job.status == JobStatus.open:
        job.status = JobStatus.in_progress
        session.add(job)
    session.commit()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/{job_id}/parts")
def add_part_line(
    job_id: int,
    description: str = Form(...),
    part_number: str = Form(""),
    quantity: float = Form(1.0),
    cost_price: float = Form(0.0),
    markup_pct: float = Form(...),
    vat_rate: float = Form(...),
    supplier_invoice_line_id: str = Form(""),
    session: Session = Depends(get_session),
):
    line_id = int(supplier_invoice_line_id) if supplier_invoice_line_id.strip().isdigit() else None
    if line_id:
        supplier_line = session.get(SupplierInvoiceLine, line_id)
        if supplier_line:
            supplier_line.allocated_job_id = job_id
            session.add(supplier_line)

    part = JobPartLine(
        job_id=job_id,
        supplier_invoice_line_id=line_id,
        part_number=part_number or None,
        description=description,
        quantity=quantity,
        cost_price=cost_price,
        markup_pct=markup_pct,
        sell_price=apply_markup(cost_price, markup_pct),
        vat_rate=vat_rate,
    )
    session.add(part)
    job = session.get(Job, job_id)
    if job and job.status == JobStatus.open:
        job.status = JobStatus.in_progress
        session.add(job)
    session.commit()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/{job_id}/allocate/{line_id}")
def allocate_supplier_line(
    job_id: int,
    line_id: int,
    markup_pct: float = Form(...),
    vat_rate: float = Form(...),
    session: Session = Depends(get_session),
):
    supplier_line = session.get(SupplierInvoiceLine, line_id)
    if supplier_line is None:
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    supplier_line.allocated_job_id = job_id
    session.add(supplier_line)

    part = JobPartLine(
        job_id=job_id,
        supplier_invoice_line_id=line_id,
        part_number=supplier_line.part_number,
        description=supplier_line.description,
        quantity=supplier_line.quantity,
        cost_price=supplier_line.unit_price,
        markup_pct=markup_pct,
        sell_price=apply_markup(supplier_line.unit_price, markup_pct),
        vat_rate=vat_rate,
    )
    session.add(part)
    job = session.get(Job, job_id)
    if job and job.status == JobStatus.open:
        job.status = JobStatus.in_progress
        session.add(job)
    session.commit()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/{job_id}/generate-invoice")
def create_invoice_from_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        return RedirectResponse("/jobs", status_code=303)
    invoice = generate_customer_invoice(session, job)
    return RedirectResponse(f"/customer-invoices/{invoice.id}", status_code=303)


@router.get("/vehicles-for-customer/{customer_id}")
def vehicles_for_customer(customer_id: int, session: Session = Depends(get_session)):
    vehicles = session.exec(select(Vehicle).where(Vehicle.customer_id == customer_id)).all()
    return [{"id": v.id, "label": f"{v.registration} - {v.make or ''} {v.model or ''}".strip()} for v in vehicles]
