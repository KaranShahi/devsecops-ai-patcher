import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from .config import settings
from .models import CustomerInvoice


def render_invoice_pdf(invoice: CustomerInvoice) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("InvoiceTitle", parent=styles["Title"], fontSize=20, spaceAfter=2)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=9, leading=12)

    story = []

    story.append(Paragraph(settings.company_name, title_style))
    company_lines = [settings.company_address_line1, settings.company_address_line2]
    if settings.company_phone:
        company_lines.append(f"Tel: {settings.company_phone}")
    if settings.company_email:
        company_lines.append(settings.company_email)
    if settings.company_vat_number:
        company_lines.append(f"VAT No: {settings.company_vat_number}")
    for line in company_lines:
        if line:
            story.append(Paragraph(line, small))
    story.append(Spacer(1, 10 * mm))

    customer = invoice.customer
    header_data = [
        ["Invoice", invoice.invoice_number],
        ["Date", invoice.invoice_date.strftime("%d %b %Y")],
        ["Due", invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "-"],
    ]
    if invoice.job and invoice.job.vehicle:
        v = invoice.job.vehicle
        header_data.append(["Vehicle", f"{v.registration} - {v.make or ''} {v.model or ''}".strip()])

    bill_to_lines = [customer.name if customer else "", ]
    if customer:
        bill_to_lines += [customer.address_line1, customer.address_line2, customer.city, customer.postcode]
    bill_to_text = "<br/>".join(l for l in bill_to_lines if l)

    top_table = Table(
        [
            [Paragraph("<b>Bill To</b><br/>" + bill_to_text, small), Table(header_data, colWidths=[25 * mm, 45 * mm])],
        ],
        colWidths=[90 * mm, 70 * mm],
    )
    top_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(top_table)
    story.append(Spacer(1, 8 * mm))

    table_data = [["Description", "Qty", "Unit Price", "VAT", "Line Total"]]
    for line in invoice.lines:
        table_data.append(
            [
                Paragraph(line.description, small),
                f"{line.quantity:g}",
                f"£{line.unit_price:.2f}",
                f"{line.vat_rate * 100:.0f}%",
                f"£{line.line_total:.2f}",
            ]
        )

    items_table = Table(table_data, colWidths=[80 * mm, 15 * mm, 25 * mm, 15 * mm, 25 * mm])
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(items_table)
    story.append(Spacer(1, 6 * mm))

    totals_data = [
        ["Subtotal", f"£{invoice.subtotal:.2f}"],
        ["VAT", f"£{invoice.vat_amount:.2f}"],
        ["Total Due", f"£{invoice.total:.2f}"],
    ]
    totals_table = Table(totals_data, colWidths=[35 * mm, 30 * mm], hAlign="RIGHT")
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    story.append(totals_table)

    if invoice.notes:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(invoice.notes, small))

    doc.build(story)
    return buffer.getvalue()
