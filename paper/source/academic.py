"""Shared plain academic layout for both working papers (reportlab)."""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import black
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
                                KeepTogether, Image, CondPageBreak)
from reportlab.platypus.flowables import Flowable

FONT, BOLD, ITAL, BI = "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"

TITLE = ParagraphStyle("title", fontName=BOLD, fontSize=16, leading=21, alignment=TA_CENTER, spaceAfter=14)
AUTHOR = ParagraphStyle("author", fontName=FONT, fontSize=12, leading=15, alignment=TA_CENTER, spaceAfter=2)
DATE = ParagraphStyle("date", fontName=ITAL, fontSize=10.5, leading=14, alignment=TA_CENTER, spaceAfter=18)
ABS_H = ParagraphStyle("absh", fontName=BOLD, fontSize=10.5, leading=13, alignment=TA_CENTER, spaceAfter=5)
ABS = ParagraphStyle("abs", fontName=FONT, fontSize=10, leading=13.5, alignment=TA_JUSTIFY, leftIndent=36, rightIndent=36, spaceAfter=6)
H1 = ParagraphStyle("h1", fontName=BOLD, fontSize=12, leading=15, spaceBefore=14, spaceAfter=6, keepWithNext=1)
H2 = ParagraphStyle("h2", fontName=BI, fontSize=11, leading=14, spaceBefore=9, spaceAfter=3, keepWithNext=1)
BODY = ParagraphStyle("body", fontName=FONT, fontSize=11, leading=15.4, alignment=TA_JUSTIFY, spaceAfter=7, firstLineIndent=0)
BULLET = ParagraphStyle("bul", parent=BODY, leftIndent=18, bulletIndent=6, spaceAfter=3)
CAP = ParagraphStyle("cap", fontName=FONT, fontSize=9.5, leading=12, alignment=TA_JUSTIFY, spaceBefore=4, spaceAfter=10)
TCAP = ParagraphStyle("tcap", fontName=FONT, fontSize=10, leading=13, alignment=TA_LEFT, spaceBefore=8, spaceAfter=4)
NOTE = ParagraphStyle("note", fontName=FONT, fontSize=8.5, leading=11, alignment=TA_JUSTIFY, spaceBefore=3, spaceAfter=10)
TC = ParagraphStyle("tc", fontName=FONT, fontSize=9, leading=11)
TH = ParagraphStyle("th", fontName=BOLD, fontSize=9, leading=11)
REF = ParagraphStyle("ref", fontName=FONT, fontSize=10, leading=13, leftIndent=18, firstLineIndent=-18, spaceAfter=5, alignment=TA_LEFT)
FOOT = ParagraphStyle("foot", fontName=FONT, fontSize=8.5, leading=10.5)
Q = ParagraphStyle("q", fontName=ITAL, fontSize=11, leading=14, spaceBefore=8, spaceAfter=2, keepWithNext=1)


def table(head, rows, widths, caption=None, note=None):
    """Three-rule (booktabs-style) table, no shading, no vertical lines."""
    data = [[Paragraph(h, TH) for h in head]] + [[Paragraph(str(v), TC) for v in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="CENTER")
    t.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 1.0, black),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, black),
        ("LINEBELOW", (0, -1), (-1, -1), 1.0, black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
    parts = []
    if caption:
        parts.append(Paragraph(caption, TCAP))
    parts.append(t)
    if note:
        parts.append(Paragraph(note, NOTE))
    else:
        parts.append(Spacer(1, 8))
    return KeepTogether(parts)


def figure(path, width, height, caption):
    return KeepTogether([Spacer(1, 4), Image(path, width=width, height=height), Paragraph(caption, CAP)])


def title_block(title, author, date, abstract, keywords, footnote=None):
    out = [Spacer(1, 40), Paragraph(title, TITLE), Paragraph(author, AUTHOR), Paragraph(date, DATE),
           Paragraph("Abstract", ABS_H)]
    for para in (abstract if isinstance(abstract, list) else [abstract]):
        out.append(Paragraph(para, ABS))
    out.append(Paragraph(f"<i>Keywords:</i> {keywords}", ABS))
    out.append(Spacer(1, 10))
    return out


def build(path, story, title, author, subject, first_page_note=None):
    def first(cv, doc):
        if first_page_note:
            cv.saveState(); cv.setLineWidth(0.5); cv.line(72, 96, 200, 96)
            cv.setFont(FONT, 8.5); y = 86
            for line in first_page_note:
                cv.drawString(72, y, line); y -= 10.5
            cv.restoreState()
        cv.saveState(); cv.setFont(FONT, 10); cv.drawCentredString(306, 40, str(doc.page)); cv.restoreState()

    def later(cv, doc):
        cv.saveState(); cv.setFont(FONT, 10); cv.drawCentredString(306, 40, str(doc.page)); cv.restoreState()

    doc = BaseDocTemplate(path, pagesize=letter, leftMargin=72, rightMargin=72, topMargin=72, bottomMargin=72,
                          title=title, author=author, subject=subject)
    fr1 = Frame(72, 110 if first_page_note else 72, 468, 610 if first_page_note else 648, id="f1")
    fr = Frame(72, 72, 468, 648, id="f")
    doc.addPageTemplates([PageTemplate("first", frames=[fr1], onPage=first, autoNextPageTemplate="later"),
                          PageTemplate("later", frames=[fr], onPage=later)])
    doc.build(story)
