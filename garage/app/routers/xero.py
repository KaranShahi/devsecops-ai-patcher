import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from .. import xero_client
from ..config import settings
from ..database import get_session
from ..models import XeroToken
from ..templating import templates

router = APIRouter(prefix="/xero", tags=["xero"])

_pending_states: set[str] = set()


@router.get("/status")
def status(request: Request, session: Session = Depends(get_session)):
    token = session.exec(select(XeroToken)).first()
    configured = bool(settings.xero_client_id and settings.xero_client_secret)
    return templates.TemplateResponse(
        request, "xero_settings.html", {"token": token, "configured": configured}
    )


@router.get("/connect")
def connect():
    if not settings.xero_client_id:
        return HTMLResponse(
            "<p>Set XERO_CLIENT_ID and XERO_CLIENT_SECRET in your .env first "
            "(create an app at https://developer.xero.com/app/manage).</p>",
            status_code=400,
        )
    state = secrets.token_urlsafe(16)
    _pending_states.add(state)
    return RedirectResponse(xero_client.authorize_url(state))


@router.get("/callback")
def callback(code: str, state: str, session: Session = Depends(get_session)):
    if state not in _pending_states:
        return HTMLResponse("Invalid OAuth state - please try connecting again.", status_code=400)
    _pending_states.discard(state)

    token_data = xero_client.exchange_code_for_token(code)
    connections = xero_client.get_connections(token_data["access_token"])
    if not connections:
        return HTMLResponse("No Xero organisation was authorised.", status_code=400)
    tenant = connections[0]

    existing = session.exec(select(XeroToken)).first()
    expires_at = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 1800))
    if existing:
        existing.access_token = token_data["access_token"]
        existing.refresh_token = token_data["refresh_token"]
        existing.expires_at = expires_at
        existing.tenant_id = tenant["tenantId"]
        existing.tenant_name = tenant.get("tenantName")
        session.add(existing)
    else:
        session.add(
            XeroToken(
                access_token=token_data["access_token"],
                refresh_token=token_data["refresh_token"],
                expires_at=expires_at,
                tenant_id=tenant["tenantId"],
                tenant_name=tenant.get("tenantName"),
            )
        )
    session.commit()
    return RedirectResponse("/xero/status", status_code=303)


@router.post("/disconnect")
def disconnect(session: Session = Depends(get_session)):
    token = session.exec(select(XeroToken)).first()
    if token:
        session.delete(token)
        session.commit()
    return RedirectResponse("/xero/status", status_code=303)
