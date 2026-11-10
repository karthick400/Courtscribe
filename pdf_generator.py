from __future__ import annotations

import io
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def generate_pdf(
    *,
    title: str,
    court_name: str,
    case_number: str,
    hearing_date: str,
    appearances: Iterable[str],
    segments: Iterable[Mapping[str, Any]] = (),
    transcript: str | None = None,
) -> bytes:
    """Render a court transcript with its header, appearances, and signature line."""
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.7 * inch,
        title=title,
    )
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "CourtHeader",
        parent=styles["Heading1"],
        alignment=TA_CENTER,
        fontSize=15,
        leading=19,
        spaceAfter=5,
    )
    centered_style = ParagraphStyle(
        "CenteredMetadata",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=10,
        leading=14,
    )
    section_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontSize=11,
        leading=14,
        spaceBefore=14,
        spaceAfter=7,
    )
    transcript_style = ParagraphStyle(
        "Transcript",
        parent=styles["BodyText"],
        fontSize=10.5,
        leading=15,
        spaceAfter=7,
    )

    story: list[Any] = [
        Paragraph(_escape(court_name) or "CourtScribe", header_style),
        Paragraph(
            f"Case No.: {_escape(case_number) or 'Not provided'}"
            f" &nbsp;&nbsp;&nbsp; Date: {_escape(hearing_date) or date.today().isoformat()}",
            centered_style,
        ),
        Spacer(1, 8),
        Paragraph(_escape(title) or "CourtScribe Transcript", centered_style),
        Paragraph("Appearances", section_style),
    ]

    appearance_rows = [
        [Paragraph(_escape(appearance), styles["BodyText"])]
        for appearance in appearances
        if str(appearance).strip()
    ]
    if appearance_rows:
        appearances_table = Table(appearance_rows, colWidths=[6.9 * inch])
        appearances_table.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(appearances_table)
    else:
        story.append(Paragraph("None provided.", transcript_style))

    story.append(Paragraph("Transcript", section_style))
    transcript_lines = _transcript_lines(segments, transcript)
    if transcript_lines:
        story.extend(
            Paragraph(line, transcript_style) for line in transcript_lines
        )
    else:
        story.append(Paragraph("No transcript entries.", transcript_style))

    story.extend(
        [
            Spacer(1, 28),
            Table(
                [["Signature: ______________________________________________"]],
                colWidths=[6.9 * inch],
                style=TableStyle(
                    [
                        ("LINEABOVE", (0, 0), (-1, -1), 0.5, colors.black),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ]
                ),
            ),
        ]
    )
    document.build(story)
    return output.getvalue()


def _transcript_lines(
    segments: Iterable[Mapping[str, Any]], transcript: str | None
) -> list[str]:
    segment_list = list(segments)
    if segment_list:
        lines = []
        for segment in segment_list:
            speaker = (
                segment.get("speaker")
                or segment.get("name")
                or segment.get("role")
                or "Unknown speaker"
            )
            text = str(segment.get("text", "")).strip()
            if text:
                lines.append(f"<b>{_escape(str(speaker))}:</b> {_escape(text)}")
        return lines
    return [_escape(line) for line in (transcript or "").splitlines() if line.strip()]


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
