from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..database import get_session
from ..models import Customer
from ..templating import templates

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("")
def list_customers(request: Request, session: Session = Depends(get_session)):
    customers = session.exec(select(Customer).order_by(Customer.name)).all()
    return templates.TemplateResponse(
        request, "customers.html", {"customers": customers}
    )


@router.post("")
def create_customer(
    name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    address_line1: str = Form(""),
    address_line2: str = Form(""),
    city: str = Form(""),
    postcode: str = Form(""),
    session: Session = Depends(get_session),
):
    customer = Customer(
        name=name,
        email=email or None,
        phone=phone or None,
        address_line1=address_line1 or None,
        address_line2=address_line2 or None,
        city=city or None,
        postcode=postcode or None,
    )
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return RedirectResponse(f"/customers/{customer.id}", status_code=303)


@router.get("/{customer_id}")
def customer_detail(customer_id: int, request: Request, session: Session = Depends(get_session)):
    customer = session.get(Customer, customer_id)
    if customer is None:
        return RedirectResponse("/customers", status_code=303)
    return templates.TemplateResponse(
        request, "customer_detail.html", {"customer": customer}
    )
