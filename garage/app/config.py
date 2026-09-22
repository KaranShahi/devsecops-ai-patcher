import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'garage.db'}")
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "data" / "uploads"))

    company_name: str = os.getenv("COMPANY_NAME", "Your Garage Ltd")
    company_address_line1: str = os.getenv("COMPANY_ADDRESS_LINE1", "")
    company_address_line2: str = os.getenv("COMPANY_ADDRESS_LINE2", "")
    company_phone: str = os.getenv("COMPANY_PHONE", "")
    company_email: str = os.getenv("COMPANY_EMAIL", "")
    company_vat_number: str = os.getenv("COMPANY_VAT_NUMBER", "")

    default_vat_rate: float = float(os.getenv("DEFAULT_VAT_RATE", "0.20"))
    default_parts_markup_pct: float = float(os.getenv("DEFAULT_PARTS_MARKUP_PCT", "25"))
    default_labor_rate: float = float(os.getenv("DEFAULT_LABOR_RATE", "65"))
    invoice_number_prefix: str = os.getenv("INVOICE_NUMBER_PREFIX", "INV-")

    xero_client_id: str = os.getenv("XERO_CLIENT_ID", "")
    xero_client_secret: str = os.getenv("XERO_CLIENT_SECRET", "")
    xero_redirect_uri: str = os.getenv("XERO_REDIRECT_URI", "http://localhost:8000/xero/callback")

    secret_key: str = os.getenv("SECRET_KEY", "change-me-in-production")


settings = Settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)
