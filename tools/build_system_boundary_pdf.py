"""Build docs/system_boundary.pdf from docs/system_boundary.md using ReportLab.

Minimal markdown subset: headings (#..####), paragraphs, tables (GFM pipes),
fenced code blocks, blockquotes, bullet lists. Good enough for this doc.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    Preformatted,
    PageBreak,
)


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "system_boundary.md"
OUT = ROOT / "docs" / "system_boundary.pdf"


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=20, spaceAfter=12, textColor=colors.HexColor("#1e3a5f")),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=15, spaceBefore=14, spaceAfter=8, textColor=colors.HexColor("#1e3a5f")),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=12, spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#2d5a3d")),
        "h4": ParagraphStyle("h4", parent=base["Heading4"], fontSize=11, spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontSize=10, leading=14),
        "bullet": ParagraphStyle("bullet", parent=base["BodyText"], fontSize=10, leading=14, leftIndent=18, bulletIndent=6),
        "quote": ParagraphStyle("quote", parent=base["BodyText"], fontSize=10, leading=14, leftIndent=14, textColor=colors.HexColor("#555555"), borderPadding=4),
        "code": ParagraphStyle("code", parent=base["Code"], fontSize=8, leading=10, backColor=colors.HexColor("#f4f4f4"), borderPadding=4, leftIndent=6, rightIndent=6),
        "tbl_header": ParagraphStyle("tbl_header", parent=base["BodyText"], fontSize=9, leading=12, textColor=colors.white, fontName="Helvetica-Bold"),
        "tbl_cell": ParagraphStyle("tbl_cell", parent=base["BodyText"], fontSize=9, leading=12),
    }
    return styles


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(text: str) -> str:
    # escape XML first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _INLINE_CODE.sub(r'<font face="Courier" size="9" backColor="#f4f4f4">\1</font>', text)
    text = _LINK.sub(r'<link href="\2" color="#1e6fb8">\1</link>', text)
    return text


def _parse_table(lines: list[str], i: int):
    header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
    sep = lines[i + 1]
    if not re.match(r"^\s*\|?\s*:?-{2,}", sep):
        return None, i
    rows = [header]
    j = i + 2
    while j < len(lines) and lines[j].strip().startswith("|"):
        row = [c.strip() for c in lines[j].strip().strip("|").split("|")]
        # pad / trim to header length
        if len(row) < len(header):
            row += [""] * (len(header) - len(row))
        rows.append(row[: len(header)])
        j += 1
    return rows, j


def build(md_path: Path, pdf_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    styles = _styles()
    story = []

    i = 0
    in_code = False
    code_buf: list[str] = []

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith("```"):
            if in_code:
                story.append(Preformatted("\n".join(code_buf), styles["code"]))
                story.append(Spacer(1, 6))
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # heading
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            content = _inline(m.group(2))
            story.append(Paragraph(content, styles[f"h{level}"]))
            i += 1
            continue

        # blockquote
        if line.startswith("> "):
            story.append(Paragraph(_inline(line[2:]), styles["quote"]))
            i += 1
            continue

        # horizontal rule
        if re.match(r"^-{3,}\s*$", line):
            story.append(Spacer(1, 6))
            i += 1
            continue

        # table
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            rows, new_i = _parse_table(lines, i)
            if rows:
                data = [[Paragraph(_inline(c), styles["tbl_header" if r == 0 else "tbl_cell"]) for c in row]
                        for r, row in enumerate(rows)]
                tbl = Table(data, repeatRows=1, hAlign="LEFT")
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(tbl)
                story.append(Spacer(1, 8))
                i = new_i
                continue

        # bullet list
        m = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if m:
            content = _inline(m.group(2))
            story.append(Paragraph(content, styles["bullet"], bulletText="•"))
            i += 1
            continue

        # numbered list
        m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if m:
            content = _inline(m.group(3))
            story.append(Paragraph(content, styles["bullet"], bulletText=f"{m.group(2)}."))
            i += 1
            continue

        # blank
        if not line.strip():
            story.append(Spacer(1, 4))
            i += 1
            continue

        # paragraph (collect consecutive non-empty, non-special lines)
        para = [line]
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if (not nxt.strip()
                    or nxt.startswith("#")
                    or nxt.lstrip().startswith("|")
                    or nxt.lstrip().startswith("> ")
                    or nxt.strip().startswith("```")
                    or re.match(r"^\s*[-*]\s+", nxt)
                    or re.match(r"^\s*\d+\.\s+", nxt)
                    or re.match(r"^-{3,}\s*$", nxt)):
                break
            para.append(nxt)
            j += 1
        story.append(Paragraph(_inline(" ".join(para)), styles["body"]))
        i = j

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title="System Boundary & Encapsulation",
        author="IgnitionChatbot",
    )
    doc.build(story)


if __name__ == "__main__":
    build(SRC, OUT)
    print(f"wrote {OUT}")
