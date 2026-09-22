from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class JobStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    completed = "completed"
    invoiced = "invoiced"
    cancelled = "cancelled"


class InvoiceStatus(str, Enum):
    draft = "draft"
    sent = "sent"
    paid = "paid"
    void = "void"


class SupplierCode(str, Enum):
    gsf = "gsf"
    eurocarparts = "eurocarparts"
    other = "other"


class Customer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    postcode: Optional[str] = None
    xero_contact_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    vehicles: list["Vehicle"] = Relationship(back_populates="customer")
    jobs: list["Job"] = Relationship(back_populates="customer")
    invoices: list["CustomerInvoice"] = Relationship(back_populates="customer")


class Vehicle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    registration: str
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    vin: Optional[str] = None
    mileage: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    customer: Optional[Customer] = Relationship(back_populates="vehicles")
    jobs: list["Job"] = Relationship(back_populates="vehicle")


class Supplier(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    code: SupplierCode = Field(default=SupplierCode.other)

    invoices: list["SupplierInvoice"] = Relationship(back_populates="supplier")


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    vehicle_id: int = Field(foreign_key="vehicle.id")
    title: str
    description: Optional[str] = None
    status: JobStatus = Field(default=JobStatus.open)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    customer: Optional[Customer] = Relationship(back_populates="jobs")
    vehicle: Optional[Vehicle] = Relationship(back_populates="jobs")
    labor_lines: list["JobLaborLine"] = Relationship(back_populates="job")
    part_lines: list["JobPartLine"] = Relationship(back_populates="job")
    invoices: list["CustomerInvoice"] = Relationship(back_populates="job")


class JobLaborLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id")
    description: str
    hours: float
    rate: float
    vat_rate: float = Field(default=0.20)

    job: Optional[Job] = Relationship(back_populates="labor_lines")

    @property
    def line_total(self) -> float:
        return round(self.hours * self.rate, 2)


class SupplierInvoice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: int = Field(foreign_key="supplier.id")
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    subtotal: float = 0.0
    vat_amount: float = 0.0
    total: float = 0.0
    currency: str = "GBP"
    source_filename: Optional[str] = None
    stored_path: Optional[str] = None
    raw_text: Optional[str] = None
    needs_review: bool = Field(default=False)
    parser_notes: Optional[str] = None
    imported_at: datetime = Field(default_factory=datetime.utcnow)

    supplier: Optional[Supplier] = Relationship(back_populates="invoices")
    lines: list["SupplierInvoiceLine"] = Relationship(back_populates="supplier_invoice")


class SupplierInvoiceLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_invoice_id: int = Field(foreign_key="supplierinvoice.id")
    line_no: int
    part_number: Optional[str] = None
    description: str
    quantity: float = 1.0
    unit_price: float = 0.0
    vat_rate: float = 0.20
    line_total: float = 0.0
    allocated_job_id: Optional[int] = Field(default=None, foreign_key="job.id")

    supplier_invoice: Optional[SupplierInvoice] = Relationship(back_populates="lines")


class JobPartLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id")
    supplier_invoice_line_id: Optional[int] = Field(default=None, foreign_key="supplierinvoiceline.id")
    part_number: Optional[str] = None
    description: str
    quantity: float = 1.0
    cost_price: float = 0.0
    markup_pct: float = 25.0
    sell_price: float = 0.0
    vat_rate: float = 0.20

    job: Optional[Job] = Relationship(back_populates="part_lines")

    @property
    def line_total(self) -> float:
        return round(self.quantity * self.sell_price, 2)


class CustomerInvoice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: Optional[int] = Field(default=None, foreign_key="job.id")
    customer_id: int = Field(foreign_key="customer.id")
    invoice_number: str = Field(index=True, unique=True)
    invoice_date: date
    due_date: Optional[date] = None
    status: InvoiceStatus = Field(default=InvoiceStatus.draft)
    subtotal: float = 0.0
    vat_amount: float = 0.0
    total: float = 0.0
    notes: Optional[str] = None
    xero_invoice_id: Optional[str] = None
    xero_invoice_number: Optional[str] = None
    xero_status: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    customer: Optional[Customer] = Relationship(back_populates="invoices")
    job: Optional[Job] = Relationship(back_populates="invoices")
    lines: list["CustomerInvoiceLine"] = Relationship(back_populates="invoice")


class CustomerInvoiceLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_id: int = Field(foreign_key="customerinvoice.id")
    description: str
    quantity: float = 1.0
    unit_price: float = 0.0
    vat_rate: float = 0.20
    line_total: float = 0.0
    account_code: Optional[str] = None

    invoice: Optional[CustomerInvoice] = Relationship(back_populates="lines")


class XeroToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    access_token: str
    refresh_token: str
    expires_at: datetime
    tenant_id: str
    tenant_name: Optional[str] = None
    connected_at: datetime = Field(default_factory=datetime.utcnow)


class AppSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str
