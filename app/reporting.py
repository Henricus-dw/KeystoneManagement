"""PDF export of the activity log (reportlab)."""
from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Palette echoing the app.
INK = colors.HexColor("#0e141e")
TEAL = colors.HexColor("#1f6e7b")
MUTED = colors.HexColor("#5a6675")
LINE = colors.HexColor("#d7dde5")
HEAD_BG = colors.HexColor("#11313a")


def _local(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d %b %Y  %H:%M")


def build_activity_pdf(activities, *, filters_desc: str, generated_by: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=16 * mm, rightMargin=16 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title="Keystone Activity Log",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=20, textColor=INK,
                        spaceAfter=2, alignment=TA_LEFT)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9, textColor=MUTED, spaceAfter=1)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=INK)
    cellmuted = ParagraphStyle("cellm", parent=cell, textColor=MUTED)
    hcell = ParagraphStyle("hcell", parent=styles["Normal"], fontSize=8, leading=10,
                           textColor=colors.white, fontName="Helvetica-Bold")

    now = datetime.now().strftime("%d %b %Y %H:%M")
    story = [
        Paragraph("Keystone Activity Log", h1),
        Paragraph("Asimotech &middot; internal audit trail", sub),
        Paragraph(f"Filters: {filters_desc}", sub),
        Paragraph(f"Generated {now} by {generated_by} &middot; {len(activities)} event(s)", sub),
        Spacer(1, 8),
    ]

    data = [[Paragraph(h, hcell) for h in ("Date / Time", "Who", "Event", "Project")]]
    for a in activities:
        who = a.user.name if a.user else "System"
        project = a.project.name if a.project else "—"
        data.append([
            Paragraph(_local(a.created_at), cellmuted),
            Paragraph(who, cell),
            Paragraph(a.summary, cell),
            Paragraph(project, cellmuted),
        ])

    table = Table(data, colWidths=[34 * mm, 38 * mm, 140 * mm, 50 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7fa")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("LINEBEFORE", (0, 0), (0, -1), 2, TEAL),
    ]))
    story.append(table)

    if not activities:
        story.append(Spacer(1, 10))
        story.append(Paragraph("No activity matched these filters.", cellmuted))

    doc.build(story)
    return buf.getvalue()
