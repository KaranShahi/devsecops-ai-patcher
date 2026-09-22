from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, func, select

from .database import get_session, init_db
from .models import Customer, CustomerInvoice, InvoiceStatus, Job, JobStatus, SupplierInvoice
from .routers import customer_invoices, customers, jobs, supplier_invoices, vehicles, xero
from .templating import templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Garage Manager", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

app.include_router(customers.router)
app.include_router(vehicles.router)
app.include_router(jobs.router)
app.include_router(supplier_invoices.router)
app.include_router(customer_invoices.router)
app.include_router(xero.router)


@app.get("/")
def dashboard(request: Request, session: Session = Depends(get_session)):
    open_jobs = session.exec(select(func.count()).select_from(Job).where(Job.status.in_([JobStatus.open, JobStatus.in_progress]))).one()
    customers_count = session.exec(select(func.count()).select_from(Customer)).one()
    unpaid_total = session.exec(
        select(func.coalesce(func.sum(CustomerInvoice.total), 0.0)).where(
            CustomerInvoice.status.in_([InvoiceStatus.draft, InvoiceStatus.sent])
        )
    ).one()
    needs_review = session.exec(
        select(func.count()).select_from(SupplierInvoice).where(SupplierInvoice.needs_review == True)  # noqa: E712
    ).one()
    recent_jobs = session.exec(select(Job).order_by(Job.created_at.desc()).limit(5)).all()
    recent_invoices = session.exec(select(CustomerInvoice).order_by(CustomerInvoice.invoice_date.desc()).limit(5)).all()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "open_jobs": open_jobs,
            "customers_count": customers_count,
            "unpaid_total": unpaid_total,
            "needs_review": needs_review,
            "recent_jobs": recent_jobs,
            "recent_invoices": recent_invoices,
        },
    )
