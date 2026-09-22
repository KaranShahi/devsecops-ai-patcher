import pytest

from app.invoicing import apply_markup, generate_customer_invoice, next_invoice_number
from app.models import Customer, Job, JobLaborLine, JobPartLine, JobStatus, Vehicle


def make_job(session):
    customer = Customer(name="Jane Driver", email="jane@example.com")
    session.add(customer)
    session.commit()
    session.refresh(customer)

    vehicle = Vehicle(customer_id=customer.id, registration="AB12CDE", make="Ford", model="Focus")
    session.add(vehicle)
    session.commit()
    session.refresh(vehicle)

    job = Job(customer_id=customer.id, vehicle_id=vehicle.id, title="Brake service")
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_apply_markup():
    assert apply_markup(20.0, 25) == 25.0
    assert apply_markup(10.0, 0) == 10.0


def test_next_invoice_number_increments(session):
    first = next_invoice_number(session)
    session.commit()
    second = next_invoice_number(session)
    session.commit()
    assert first != second
    assert first.startswith("INV-")


def test_generate_customer_invoice_computes_totals(session):
    job = make_job(session)
    session.add(JobLaborLine(job_id=job.id, description="Remove & replace pads", hours=2, rate=60.0, vat_rate=0.20))
    session.add(
        JobPartLine(
            job_id=job.id,
            description="Front brake pads",
            part_number="BP-100",
            quantity=1,
            cost_price=20.0,
            markup_pct=25,
            sell_price=apply_markup(20.0, 25),
            vat_rate=0.20,
        )
    )
    session.commit()
    session.refresh(job)

    invoice = generate_customer_invoice(session, job)

    # labour: 2 * 60 = 120, parts: 1 * 25 = 25 -> subtotal 145, VAT 20% = 29, total 174
    assert invoice.subtotal == 145.0
    assert invoice.vat_amount == 29.0
    assert invoice.total == 174.0
    assert len(invoice.lines) == 2
    assert job.status == JobStatus.invoiced


def test_generate_customer_invoice_rejects_empty_job(session):
    job = make_job(session)
    with pytest.raises(ValueError):
        generate_customer_invoice(session, job)
