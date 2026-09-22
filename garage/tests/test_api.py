def test_dashboard_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Dashboard" in resp.text


def test_customer_and_job_flow(client):
    resp = client.post(
        "/customers",
        data={"name": "Jane Driver", "email": "jane@example.com", "phone": "07123456789"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    customer_url = resp.headers["location"]
    customer_id = int(customer_url.rsplit("/", 1)[-1])

    resp = client.get(customer_url)
    assert resp.status_code == 200
    assert "Jane Driver" in resp.text

    resp = client.post(
        "/vehicles",
        data={"customer_id": customer_id, "registration": "ab12 cde", "make": "Ford", "model": "Focus"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp = client.get(customer_url)
    assert "AB12CDE" in resp.text

    resp = client.get("/jobs/new")
    assert resp.status_code == 200
    assert "Jane Driver" in resp.text


def test_supplier_invoices_page_loads(client):
    resp = client.get("/supplier-invoices")
    assert resp.status_code == 200
    assert "Import an invoice" in resp.text


def test_xero_status_page_without_credentials(client):
    resp = client.get("/xero/status")
    assert resp.status_code == 200
    assert "Xero" in resp.text


def test_full_job_to_invoice_flow(client):
    resp = client.post("/customers", data={"name": "Bob Mechanic Customer"}, follow_redirects=False)
    customer_id = int(resp.headers["location"].rsplit("/", 1)[-1])

    resp = client.post(
        "/vehicles",
        data={"customer_id": customer_id, "registration": "XY99ZZZ"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    vehicles = client.get(f"/jobs/vehicles-for-customer/{customer_id}").json()
    assert len(vehicles) == 1
    vehicle_id = vehicles[0]["id"]

    resp = client.post(
        "/jobs",
        data={"customer_id": customer_id, "vehicle_id": vehicle_id, "title": "Service"},
        follow_redirects=False,
    )
    job_id = int(resp.headers["location"].rsplit("/", 1)[-1])

    resp = client.post(
        f"/jobs/{job_id}/labor",
        data={"description": "Oil & filter change", "hours": 1, "rate": 60},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp = client.post(f"/jobs/{job_id}/generate-invoice", follow_redirects=False)
    assert resp.status_code == 303
    invoice_url = resp.headers["location"]

    resp = client.get(invoice_url)
    assert resp.status_code == 200
    assert "£72.00" in resp.text  # 60 * 1.20 VAT

    resp = client.get(f"{invoice_url}/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
