from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from ..database import get_session
from ..models import Vehicle

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.post("")
def create_vehicle(
    customer_id: int = Form(...),
    registration: str = Form(...),
    make: str = Form(""),
    model: str = Form(""),
    year: str = Form(""),
    vin: str = Form(""),
    mileage: str = Form(""),
    session: Session = Depends(get_session),
):
    vehicle = Vehicle(
        customer_id=customer_id,
        registration=registration.upper().replace(" ", ""),
        make=make or None,
        model=model or None,
        year=int(year) if year.strip().isdigit() else None,
        vin=vin or None,
        mileage=int(mileage) if mileage.strip().isdigit() else None,
    )
    session.add(vehicle)
    session.commit()
    session.refresh(vehicle)
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)
