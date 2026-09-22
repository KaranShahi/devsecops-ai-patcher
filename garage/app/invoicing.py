"""Core invoicing math: turning a job's labor + parts lines into a customer invoice.

Kept separate from the routers so the VAT/total arithmetic has a single,
testable home instead of being inlined into request handlers.
"""
from datetime import date, timedelta

from sqlmodel import Session, select

from .config import settings
from .models import (
    AppSetting,
    CustomerInvoice,
    CustomerInvoiceLine,
    InvoiceStatus,
    Job,
    JobStatus,
)

INVOICE_COUNTER_KEY = "next_invoice_seq"


def next_invoice_number(session: Session) -> str:
    setting = session.get(AppSetting, INVOICE_COUNTER_KEY)
    seq = int(setting.value) if setting else 1
    if setting:
        setting.value = str(seq + 1)
    else:
        setting = AppSetting(key=INVOICE_COUNTER_KEY, value=str(seq + 1))
    session.add(setting)
    return f"{settings.invoice_number_prefix}{seq:04d}"


def compute_totals(lines: list[CustomerInvoiceLine]) -> tuple[float, float, float]:
    """Return (subtotal, vat_amount, total) rounded to pence, VAT computed per line."""
    subtotal = 0.0
    vat_amount = 0.0
    for line in lines:
        line.line_total = round(line.quantity * line.unit_price, 2)
        subtotal += line.line_total
        vat_amount += line.line_total * line.vat_rate
    subtotal = round(subtotal, 2)
    vat_amount = round(vat_amount, 2)
    total = round(subtotal + vat_amount, 2)
    return subtotal, vat_amount, total


def generate_customer_invoice(session: Session, job: Job, due_in_days: int = 14) -> CustomerInvoice:
    if not job.labor_lines and not job.part_lines:
        raise ValueError("Job has no labor or parts lines to invoice")

    lines: list[CustomerInvoiceLine] = []
    for labor in job.labor_lines:
        lines.append(
            CustomerInvoiceLine(
                description=f"Labour: {labor.description}",
                quantity=labor.hours,
                unit_price=labor.rate,
                vat_rate=labor.vat_rate,
                account_code="200",
            )
        )
    for part in job.part_lines:
        desc = part.description
        if part.part_number:
            desc = f"{desc} ({part.part_number})"
        lines.append(
            CustomerInvoiceLine(
                description=f"Parts: {desc}",
                quantity=part.quantity,
                unit_price=part.sell_price,
                vat_rate=part.vat_rate,
                account_code="310",
            )
        )

    subtotal, vat_amount, total = compute_totals(lines)

    invoice = CustomerInvoice(
        job_id=job.id,
        customer_id=job.customer_id,
        invoice_number=next_invoice_number(session),
        invoice_date=date.today(),
        due_date=date.today() + timedelta(days=due_in_days),
        status=InvoiceStatus.draft,
        subtotal=subtotal,
        vat_amount=vat_amount,
        total=total,
    )
    session.add(invoice)
    session.flush()  # assign invoice.id

    for line in lines:
        line.invoice_id = invoice.id
        session.add(line)

    job.status = JobStatus.invoiced
    session.add(job)

    session.commit()
    session.refresh(invoice)
    return invoice


def apply_markup(cost_price: float, markup_pct: float) -> float:
    return round(cost_price * (1 + markup_pct / 100), 2)
