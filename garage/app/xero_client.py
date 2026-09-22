"""Xero API integration: OAuth2 authorization-code flow + Accounting API calls.

Requires a Xero app (https://developer.xero.com/app/manage) with these
scopes: openid profile email accounting.contacts accounting.transactions offline_access

This calls Xero's real HTTP API directly (no SDK dependency) so it's easy
to see exactly what's sent. It is NOT wired to any MCP server - the Xero
MCP tools available in this environment are read-only reporting tools and
cannot create contacts or invoices, so writes go straight to Xero's API
using the garage's own registered app credentials.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import requests
from sqlmodel import Session, select

from .config import settings
from .models import CustomerInvoice, XeroToken

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
API_BASE = "https://api.xero.com/api.xro/2.0"

SCOPES = "openid profile email accounting.contacts accounting.transactions offline_access"


class XeroError(RuntimeError):
    pass


class XeroNotConnected(XeroError):
    pass


def authorize_url(state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.xero_client_id,
        "redirect_uri": settings.xero_redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    query = "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items())
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code_for_token(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.xero_redirect_uri,
        },
        auth=(settings.xero_client_id, settings.xero_client_secret),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _refresh(token: XeroToken) -> XeroToken:
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": token.refresh_token},
        auth=(settings.xero_client_id, settings.xero_client_secret),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token.access_token = data["access_token"]
    token.refresh_token = data.get("refresh_token", token.refresh_token)
    token.expires_at = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 1800))
    return token


def get_connections(access_token: str) -> list[dict]:
    resp = requests.get(CONNECTIONS_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_valid_token(session: Session) -> XeroToken:
    token = session.exec(select(XeroToken)).first()
    if token is None:
        raise XeroNotConnected("Xero is not connected yet - visit /xero/connect.")
    if token.expires_at <= datetime.utcnow() + timedelta(seconds=60):
        token = _refresh(token)
        session.add(token)
        session.commit()
        session.refresh(token)
    return token


def _headers(token: XeroToken) -> dict:
    return {
        "Authorization": f"Bearer {token.access_token}",
        "Xero-tenant-id": token.tenant_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def find_or_create_contact(session: Session, token: XeroToken, customer) -> str:
    """Returns the Xero ContactID for a Customer, creating it in Xero if needed."""
    if customer.xero_contact_id:
        return customer.xero_contact_id

    payload = {
        "Contacts": [
            {
                "Name": customer.name,
                "EmailAddress": customer.email or "",
                "Phones": (
                    [{"PhoneType": "DEFAULT", "PhoneNumber": customer.phone}] if customer.phone else []
                ),
                "Addresses": [
                    {
                        "AddressType": "STREET",
                        "AddressLine1": customer.address_line1 or "",
                        "AddressLine2": customer.address_line2 or "",
                        "City": customer.city or "",
                        "PostalCode": customer.postcode or "",
                    }
                ],
            }
        ]
    }
    resp = requests.post(f"{API_BASE}/Contacts", headers=_headers(token), json=payload, timeout=30)
    if not resp.ok:
        raise XeroError(f"Xero rejected the contact: {resp.status_code} {resp.text}")
    contact_id = resp.json()["Contacts"][0]["ContactID"]
    customer.xero_contact_id = contact_id
    session.add(customer)
    session.commit()
    return contact_id


def push_customer_invoice(session: Session, invoice: CustomerInvoice) -> dict:
    """Creates (or updates, if already synced) this invoice in Xero as an ACCREC invoice."""
    token = get_valid_token(session)
    contact_id = find_or_create_contact(session, token, invoice.customer)

    line_items = [
        {
            "Description": line.description,
            "Quantity": line.quantity,
            "UnitAmount": line.unit_price,
            "AccountCode": line.account_code or "200",
            "TaxType": "OUTPUT2" if line.vat_rate and line.vat_rate > 0 else "NONE",
        }
        for line in invoice.lines
    ]

    body = {
        "Type": "ACCREC",
        "Contact": {"ContactID": contact_id},
        "Date": invoice.invoice_date.isoformat(),
        "DueDate": (invoice.due_date or invoice.invoice_date).isoformat(),
        "InvoiceNumber": invoice.invoice_number,
        "Reference": f"Job #{invoice.job_id}" if invoice.job_id else "",
        "LineAmountTypes": "Exclusive",
        "LineItems": line_items,
        "Status": "AUTHORISED",
    }
    if invoice.xero_invoice_id:
        body["InvoiceID"] = invoice.xero_invoice_id

    resp = requests.post(f"{API_BASE}/Invoices", headers=_headers(token), json={"Invoices": [body]}, timeout=30)
    if not resp.ok:
        raise XeroError(f"Xero rejected the invoice: {resp.status_code} {resp.text}")

    xero_invoice = resp.json()["Invoices"][0]
    invoice.xero_invoice_id = xero_invoice["InvoiceID"]
    invoice.xero_invoice_number = xero_invoice.get("InvoiceNumber")
    invoice.xero_status = xero_invoice.get("Status")
    session.add(invoice)
    session.commit()
    return xero_invoice
