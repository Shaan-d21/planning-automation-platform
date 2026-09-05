"""Build Document 07: Data Review, Smart Grid, Reconciliation, and Reporting."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from docs.builders.build_document_01 import (  # noqa: E402
    BLUE,
    BLUE_DARK,
    BLUE_LIGHT,
    GOLD,
    GOLD_LIGHT,
    INK,
    LINE,
    MUTED,
    NAVY,
    ORANGE,
    ORANGE_LIGHT,
    RED,
    RED_LIGHT,
    TEAL,
    TEAL_LIGHT,
    WHITE,
    add_bullets,
    add_callout,
    add_figure,
    add_numbered,
    add_page_number,
    add_para,
    add_table,
    configure_page,
    font,
    set_cell_margins,
    set_picture_alt_text,
    set_run_font,
    wrap,
)
from docs.builders.build_document_03 import (  # noqa: E402
    compact_trailing_empty_paragraph,
    configure_styles,
    patch_list_numbering,
)


OUTPUT_DIR = ROOT / "outputs" / "documentation" / "document-07"
ASSET_DIR = OUTPUT_DIR / "assets"
OUTPUT_FILE = OUTPUT_DIR / (
    "BISP_EPM_Automation_Document_07_Data_Review_Smart_Grid_"
    "Reconciliation_and_Reporting.docx"
)
LOGO = ROOT / "app" / "web" / "static" / "images" / "bisp-logo.png"

NAVY_HEX = f"#{NAVY}"
BLUE_HEX = f"#{BLUE}"
MUTED_HEX = f"#{MUTED}"
TEAL_HEX = f"#{TEAL}"
ORANGE_HEX = f"#{ORANGE}"
RED_HEX = f"#{RED}"


def _arrow(draw: ImageDraw.ImageDraw, start, end, color=BLUE_HEX, width=5) -> None:
    draw.line((*start, *end), fill=color, width=width)
    x2, y2 = end
    x1, y1 = start
    if abs(x2 - x1) >= abs(y2 - y1):
        direction = 1 if x2 > x1 else -1
        points = [(x2, y2), (x2 - 17 * direction, y2 - 10), (x2 - 17 * direction, y2 + 10)]
    else:
        direction = 1 if y2 > y1 else -1
        points = [(x2, y2), (x2 - 10, y2 - 17 * direction), (x2 + 10, y2 - 17 * direction)]
    draw.polygon(points, fill=color)


def _title(draw: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    draw.text((55, 34), title, font=font(34, bold=True), fill=NAVY_HEX)
    y = 82
    for line in wrap(draw, subtitle, font(19), 1280):
        draw.text((55, y), line, font=font(19), fill=MUTED_HEX)
        y += 26


def _card(
    draw: ImageDraw.ImageDraw,
    bounds,
    title: str,
    body: str,
    *,
    color: str,
    label: str = "",
    fill: str = "#FFFFFF",
) -> None:
    x1, y1, x2, y2 = bounds
    draw.rounded_rectangle(bounds, radius=18, fill=fill, outline=color, width=3)
    y = y1 + 18
    if label:
        draw.text((x1 + 20, y), label.upper(), font=font(15, bold=True), fill=color)
        y += 27
    for line in wrap(draw, title, font(22, bold=True), x2 - x1 - 40):
        draw.text((x1 + 20, y), line, font=font(22, bold=True), fill=NAVY_HEX)
        y += 29
    y += 5
    for line in wrap(draw, body, font(16), x2 - x1 - 40):
        draw.text((x1 + 20, y), line, font=font(16), fill=MUTED_HEX)
        y += 22


def create_review_journey(path: Path) -> None:
    image = Image.new("RGB", (1400, 740), "#F8FAFD")
    draw = ImageDraw.Draw(image)
    _title(draw, "From business question to reviewed evidence", "Data Review builds one bounded, read-only Oracle Planning slice and then applies the review method the user chooses.")
    steps = [
        (55, "Choose cube", "Fetch current plan types visible to the configured Oracle identity.", BLUE_HEX),
        (375, "Place dimensions", "Put every dimension in POV, rows, or columns exactly once.", BLUE_HEX),
        (695, "Select members", "Browse live Oracle members; POV takes one and axes can take many.", ORANGE_HEX),
        (1015, "Review and prove", "Inspect the grid, validate quality, compare cubes, or export Excel.", TEAL_HEX),
    ]
    for index, (x, title, body, color) in enumerate(steps, 1):
        _card(draw, (x, 175, x + 275, 475), title, body, color=color, label=f"STEP {index}")
        if index < len(steps):
            _arrow(draw, (x + 280, 325), (x + 310, 325), color="#9AA9BF", width=4)
    draw.rounded_rectangle((185, 545, 1215, 665), radius=20, fill=f"#{TEAL_LIGHT}", outline=TEAL_HEX, width=3)
    draw.text((220, 570), "READ-ONLY GUARANTEE", font=font(16, bold=True), fill=TEAL_HEX)
    draw.text((220, 610), "The platform requests Planning data for display and evidence. It does not submit changed cell values back to Oracle.", font=font(20, bold=True), fill=NAVY_HEX)
    image.save(path)


def create_layout_model(path: Path) -> None:
    image = Image.new("RGB", (1400, 840), "#F8FAFD")
    draw = ImageDraw.Draw(image)
    _title(draw, "Every dimension has one home", "Smart layout prefers Period for columns and Account for rows when Oracle exposes them; remaining dimensions begin in POV.")
    _card(draw, (55, 150, 420, 390), "Point of view", "One fixed member for dimensions such as Scenario, Version, Entity, Year, Currency, or View.", color=TEAL_HEX, label="CONTEXT", fill=f"#{TEAL_LIGHT}")
    _card(draw, (520, 150, 885, 390), "Columns", "One or more members per dimension. Period is the common spreadsheet-style choice.", color=BLUE_HEX, label="ACROSS THE SHEET", fill=f"#{BLUE_LIGHT}")
    _card(draw, (985, 150, 1350, 390), "Rows", "One or more members per dimension. Account, Product, or Entity commonly appears here.", color=ORANGE_HEX, label="DOWN THE SHEET", fill=f"#{ORANGE_LIGHT}")
    draw.rounded_rectangle((110, 490, 1290, 735), radius=20, fill="#FFFFFF", outline="#C8D5E8", width=3)
    draw.text((145, 520), "EXAMPLE SELECTION", font=font(16, bold=True), fill=BLUE_HEX)
    values = [
        ("POV", "Scenario = Forecast | Version = Working | Year = FY27 | Entity = Sales East", TEAL_HEX),
        ("Columns", "Period = Jan, Feb, Mar", BLUE_HEX),
        ("Rows", "Account = Revenue, Cost of Sales, Gross Profit | Product = Product A, Product B", ORANGE_HEX),
    ]
    y = 570
    for label, value, color in values:
        draw.rounded_rectangle((145, y, 280, y + 45), radius=12, fill=color)
        draw.text((168, y + 11), label, font=font(17, bold=True), fill="#FFFFFF")
        draw.text((310, y + 11), value, font=font(17), fill=NAVY_HEX)
        y += 62
    draw.text((145, 765), "Rule: a dimension cannot appear twice, and every selected dimension needs at least one member.", font=font(18, bold=True), fill=RED_HEX)
    image.save(path)


def create_grid_anatomy(path: Path) -> None:
    image = Image.new("RGB", (1400, 870), "#F8FAFD")
    draw = ImageDraw.Draw(image)
    _title(draw, "Smart Grid anatomy", "A familiar spreadsheet-like view adds Planning context, row search, paging, refresh, and a selected-cell formula strip.")
    draw.rounded_rectangle((55, 145, 1345, 815), radius=22, fill="#FFFFFF", outline="#BBC9DC", width=3)
    # POV strip
    pov = [("Scenario", "Forecast"), ("Version", "Working"), ("Year", "FY27"), ("Entity", "Sales East")]
    x = 85
    for dimension, member in pov:
        draw.rounded_rectangle((x, 175, x + 245, 245), radius=12, fill=f"#{BLUE_LIGHT}", outline="#C6D6F3", width=2)
        draw.text((x + 14, 187), dimension, font=font(14, bold=True), fill=MUTED_HEX)
        draw.text((x + 14, 214), member, font=font(18, bold=True), fill=NAVY_HEX)
        x += 265
    # Formula bar
    draw.rounded_rectangle((85, 275, 1315, 330), radius=10, fill="#F3F6FB", outline="#D2DCEB", width=2)
    draw.text((105, 290), "fx", font=font(20, bold=True), fill=BLUE_HEX)
    draw.text((160, 291), "Gross Profit x Mar x Product A", font=font(17, bold=True), fill=NAVY_HEX)
    draw.text((800, 291), "1,240.00", font=font(18, bold=True), fill=TEAL_HEX)
    # Grid
    left, top = 85, 365
    row_w, col_w, row_h = 315, 225, 66
    headers = ["Jan", "Feb", "Mar", "Apr"]
    draw.rectangle((left, top, left + row_w, top + row_h), fill=NAVY_HEX)
    draw.text((left + 20, top + 21), "Account / Product", font=font(17, bold=True), fill="#FFFFFF")
    for index, header in enumerate(headers):
        x1 = left + row_w + index * col_w
        draw.rectangle((x1, top, x1 + col_w, top + row_h), fill=NAVY_HEX)
        draw.text((x1 + 85, top + 21), header, font=font(17, bold=True), fill="#FFFFFF")
    rows = [
        ("Revenue | Product A", ["4,500", "4,820", "5,040", "5,300"]),
        ("Cost of Sales | Product A", ["2,100", "2,240", "2,360", "2,500"]),
        ("Gross Profit | Product A", ["2,400", "2,580", "2,680", "2,800"]),
        ("Gross Profit | Product B", ["1,080", "1,160", "1,240", "-"]),
    ]
    for r_index, (label, values) in enumerate(rows, 1):
        y1 = top + r_index * row_h
        fill = "#F4F7FB" if r_index % 2 else "#FFFFFF"
        draw.rectangle((left, y1, left + row_w, y1 + row_h), fill=fill, outline="#D8E0EC")
        draw.text((left + 16, y1 + 22), label, font=font(16, bold=True), fill=NAVY_HEX)
        for c_index, value in enumerate(values):
            x1 = left + row_w + c_index * col_w
            cell_fill = "#FFF3E4" if value == "-" else fill
            draw.rectangle((x1, y1, x1 + col_w, y1 + row_h), fill=cell_fill, outline="#D8E0EC")
            color = ORANGE_HEX if value == "-" else NAVY_HEX
            draw.text((x1 + 135, y1 + 22), value, font=font(17, bold=True), fill=color)
    draw.text((85, 730), "50 rows per page  |  Search row members  |  Refresh live data  |  Export Excel", font=font(17, bold=True), fill=MUTED_HEX)
    draw.text((85, 775), "The dash represents a missing Planning value; selecting a cell reveals its full row and column intersection.", font=font(17), fill=NAVY_HEX)
    image.save(path)


def create_quality_model(path: Path) -> None:
    image = Image.new("RGB", (1400, 770), "#F8FAFD")
    draw = ImageDraw.Draw(image)
    _title(draw, "Data-quality checks and verdicts", "The platform evaluates every returned cell, counts all exceptions, and returns a bounded detail list for review.")
    checks = [
        ((55, 165, 345, 395), "Missing value", "Enabled by default. A selected intersection with no value is a failure.", RED_HEX, "FAIL"),
        ((385, 165, 675, 395), "Zero value", "Optional. Zero is a warning so the user can review and acknowledge it.", ORANGE_HEX, "WARNING"),
        ((715, 165, 1005, 395), "Minimum / maximum", "Optional numeric thresholds. Values outside the accepted range fail.", RED_HEX, "FAIL"),
        ((1045, 165, 1340, 395), "Non-numeric", "Fails when a numeric rule is active but the returned value cannot be interpreted as a number.", RED_HEX, "FAIL"),
    ]
    for bounds, title, body, color, label in checks:
        _card(draw, bounds, title, body, color=color, label=label)
    _arrow(draw, (700, 430), (700, 500), color=BLUE_HEX)
    verdicts = [
        ((145, 535, 445, 680), "PASS", "No failures or warnings", TEAL_HEX, f"#{TEAL_LIGHT}"),
        ((550, 535, 850, 680), "WARNING", "Warnings only", ORANGE_HEX, f"#{GOLD_LIGHT}"),
        ((955, 535, 1255, 680), "FAIL", "One or more failures", RED_HEX, f"#{RED_LIGHT}"),
    ]
    for bounds, title, body, color, fill in verdicts:
        _card(draw, bounds, title, body, color=color, fill=fill)
    image.save(path)


def create_reconciliation_model(path: Path) -> None:
    image = Image.new("RGB", (1400, 820), "#F8FAFD")
    draw = ImageDraw.Draw(image)
    _title(draw, "Source-to-target reconciliation", "The same POV, rows, and columns are exported from two cubes and compared at corresponding intersections.")
    _card(draw, (55, 165, 420, 390), "Source cube", "Plan1 | Forecast | FY27 | Jan-Mar | selected accounts and products", color=BLUE_HEX, label="LIVE SLICE", fill=f"#{BLUE_LIGHT}")
    _card(draw, (980, 165, 1345, 390), "Target cube", "Reporting | same POV, row headers, column headers, and member order", color=TEAL_HEX, label="LIVE SLICE", fill=f"#{TEAL_LIGHT}")
    _arrow(draw, (435, 275), (650, 275), color=BLUE_HEX)
    _arrow(draw, (965, 275), (750, 275), color=TEAL_HEX)
    _card(draw, (560, 175, 840, 380), "Compare", "Difference = source - target. A non-negative business tolerance controls acceptable variance.", color=ORANGE_HEX, label="DECIMAL MATH")
    examples = [
        ("1,000.0000000000002", "1,000", "Transport noise", "MATCH", TEAL_HEX),
        ("1,250", "1,245", "Difference 5; tolerance 10", "MATCH", TEAL_HEX),
        ("1,250", "1,230", "Difference 20; tolerance 10", "DIFFERENCE", RED_HEX),
        ("Missing", "Missing", "Both sides missing", "MATCH", TEAL_HEX),
    ]
    y = 490
    widths = [300, 300, 420, 220]
    x_starts = [55, 355, 655, 1075]
    headers = ["Source", "Target", "Reason", "Result"]
    for x, width, label in zip(x_starts, widths, headers):
        draw.rectangle((x, y, x + width, y + 50), fill=NAVY_HEX)
        draw.text((x + 15, y + 15), label, font=font(16, bold=True), fill="#FFFFFF")
    for row_index, (source, target, reason, result, color) in enumerate(examples, 1):
        y1 = y + row_index * 50
        fill = "#FFFFFF" if row_index % 2 else "#F2F5FA"
        values = [source, target, reason, result]
        for x, width, value in zip(x_starts, widths, values):
            draw.rectangle((x, y1, x + width, y1 + 50), fill=fill, outline="#D6DFEB")
            draw.text((x + 15, y1 + 15), value, font=font(15, bold=value == result), fill=color if value == result else NAVY_HEX)
    draw.text((55, 735), "Tiny relative differences caused by number transport are ignored with a technical epsilon; genuine business differences still use the selected tolerance.", font=font(17, bold=True), fill=MUTED_HEX)
    image.save(path)


def create_task_evidence_model(path: Path) -> None:
    image = Image.new("RGB", (1400, 770), "#F8FAFD")
    draw = ImageDraw.Draw(image)
    _title(draw, "Assigned validation becomes governed evidence", "A Planning responsibility can reopen the exact source slice and required method, then store summary evidence without storing cell values.")
    flow = [
        (55, "Open My Work", "Assigned task carries the Planning task ID.", BLUE_HEX),
        (330, "Restore context", "Configured source slice and method are loaded.", BLUE_HEX),
        (605, "Run validation", "Oracle data is read live and checked.", ORANGE_HEX),
        (880, "Record summary", "Selection, criteria, counts, status, user, and time.", TEAL_HEX),
        (1155, "Complete", "Only Pass or acknowledged Warning can finish the task.", TEAL_HEX),
    ]
    for index, (x, title, body, color) in enumerate(flow):
        _card(draw, (x, 175, x + 220, 465), title, body, color=color, label=str(index + 1))
        if index < len(flow) - 1:
            _arrow(draw, (x + 225, 320), (x + 265, 320), color="#9AA9BF", width=4)
    draw.rounded_rectangle((170, 555, 1230, 685), radius=20, fill=f"#{GOLD_LIGHT}", outline=ORANGE_HEX, width=3)
    draw.text((205, 580), "GOVERNANCE RULE", font=font(16, bold=True), fill=ORANGE_HEX)
    draw.text((205, 620), "Fail blocks completion. A warning needs explicit acknowledgement. Only the latest validation can be acknowledged.", font=font(20, bold=True), fill=NAVY_HEX)
    image.save(path)


def create_output_map(path: Path) -> None:
    image = Image.new("RGB", (1400, 800), "#F8FAFD")
    draw = ImageDraw.Draw(image)
    _title(draw, "Choose the right Excel output", "The current application produces different workbooks for analysis, evidence, and repeatable reporting.")
    outputs = [
        ((55, 155, 650, 345), "Smart Grid export", "Re-reads the selected live cube slice and creates a formatted Report sheet plus Report Metadata.", BLUE_HEX, "AD-HOC DATA"),
        ((750, 155, 1345, 345), "Quality evidence", "Creates Validation Summary and Exceptions worksheets with status, criteria, counts, POV, and bounded issue details.", ORANGE_HEX, "CHECK RESULTS"),
        ((55, 435, 650, 625), "Comparison evidence", "Creates Comparison Summary and Cell Comparison worksheets for the bounded differences returned by the current request.", RED_HEX, "RECONCILIATION"),
        ((750, 435, 1345, 625), "Report Generation", "Runs as a monitored read-only operation from a registered report definition or supported live Planning form and retains the workbook in Jobs & Activity.", TEAL_HEX, "REPEATABLE REPORT"),
    ]
    for bounds, title, body, color, label in outputs:
        _card(draw, bounds, title, body, color=color, label=label)
    draw.rounded_rectangle((235, 690, 1165, 760), radius=15, fill=f"#{BLUE_LIGHT}", outline=BLUE_HEX, width=2)
    draw.text((280, 713), "Every workbook is a point-in-time output. Refresh or regenerate when Oracle data changes.", font=font(19, bold=True), fill=NAVY_HEX)
    image.save(path)


def create_assets() -> dict[str, Path]:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    assets = {
        "journey": ASSET_DIR / "data_review_journey.png",
        "layout": ASSET_DIR / "dimension_layout.png",
        "grid": ASSET_DIR / "smart_grid_anatomy.png",
        "quality": ASSET_DIR / "quality_checks.png",
        "reconciliation": ASSET_DIR / "reconciliation.png",
        "task": ASSET_DIR / "task_validation_evidence.png",
        "outputs": ASSET_DIR / "excel_output_map.png",
    }
    create_review_journey(assets["journey"])
    create_layout_model(assets["layout"])
    create_grid_anatomy(assets["grid"])
    create_quality_model(assets["quality"])
    create_reconciliation_model(assets["reconciliation"])
    create_task_evidence_model(assets["task"])
    create_output_map(assets["outputs"])
    return assets


def add_page_break(doc: Document) -> None:
    doc.add_page_break()


def add_chapter(doc: Document, number: int, title: str, lead: str) -> None:
    kicker = doc.add_paragraph(f"CHAPTER {number:02d}", style="Kicker")
    kicker.paragraph_format.page_break_before = True
    doc.add_paragraph(title, style="Heading 1")
    doc.add_paragraph(lead, style="Lead")


def add_heading(doc: Document, text: str, level: int = 2) -> None:
    doc.add_paragraph(text, style=f"Heading {level}")


def configure_header_footer(section) -> None:
    section.different_first_page_header_footer = True
    header = section.header
    paragraph = header.paragraphs[0]
    paragraph.text = "BISP SOLUTIONS  /  DATA REVIEW, SMART GRID, RECONCILIATION, AND REPORTING"
    set_run_font(paragraph.runs[0], size=8.5, color=MUTED, bold=True)
    footer = section.footer
    table = footer.add_table(rows=1, cols=2, width=Inches(6.5))
    table.columns[0].width = Inches(5.7)
    table.columns[1].width = Inches(0.8)
    left = table.cell(0, 0).paragraphs[0]
    left.text = "Document 07  |  Data Review, Smart Grid, Reconciliation, and Reporting"
    set_run_font(left.runs[0], size=8.5, color=MUTED)
    right = table.cell(0, 1).paragraphs[0]
    right.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    add_page_number(right)
    for cell in table.rows[0].cells:
        set_cell_margins(cell, top=0, start=0, bottom=0, end=0)
        tc_pr = cell._tc.get_or_add_tcPr()
        borders = OxmlElement("w:tcBorders")
        top = OxmlElement("w:top")
        top.set(qn("w:val"), "single")
        top.set(qn("w:sz"), "6")
        top.set(qn("w:color"), LINE)
        borders.append(top)
        tc_pr.append(borders)


def add_cover(doc: Document) -> None:
    if LOGO.is_file():
        paragraph = doc.add_paragraph()
        run = paragraph.add_run()
        run.add_picture(str(LOGO), width=Inches(1.75))
        set_picture_alt_text(run, "BISP Solutions company logo")
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(30)
    doc.add_paragraph("PLATFORM HANDBOOK", style="Kicker")
    doc.add_paragraph("Data Review, Smart Grid,\nReconciliation, and Reporting", style="Title")
    doc.add_paragraph("BISP Solutions Oracle EPM Automation Platform", style="Subtitle")
    doc.add_paragraph(
        "A beginner-friendly guide to building live Planning data slices with mouse-first selectors, reviewing them in a spreadsheet-style grid, validating data quality, comparing source and target cubes, governing assigned validation, and producing professional Excel outputs.",
        style="Lead",
    )
    add_table(
        doc,
        ["Document", "Implementation snapshot", "Audience"],
        [["07 of the platform handbook", "3 September 2026", "Planners, FP&A reviewers, Oracle EPM consultants, administrators, auditors, and support teams"]],
        [2200, 2100, 5060],
    )
    add_callout(
        doc,
        "Current implementation only",
        "This volume reflects the current React Data Review and Reports workspaces, FastAPI contracts, live Oracle Planning data-slice services, PostgreSQL task-validation evidence, Excel renderers, and current EPM Assistant tools. It excludes the retired interface.",
        fill=BLUE_LIGHT,
        accent=BLUE,
    )
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(14)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(
        paragraph.add_run("BISP Solutions  |  Live Planning insight without writeback"),
        size=10,
        color=MUTED,
        bold=True,
    )


def _finish_document(doc: Document) -> None:
    compact_trailing_empty_paragraph(doc)
    for table in doc.tables:
        header_pr = table.rows[0]._tr.get_or_add_trPr()
        if header_pr.find(qn("w:tblHeader")) is None:
            header = OxmlElement("w:tblHeader")
            header.set(qn("w:val"), "true")
            header_pr.append(header)
        for row in table.rows:
            tr_pr = row._tr.get_or_add_trPr()
            if tr_pr.find(qn("w:cantSplit")) is None:
                tr_pr.append(OxmlElement("w:cantSplit"))
    for section in doc.sections:
        for footer in (section.footer, section.even_page_footer):
            for table in footer.tables:
                header_pr = table.rows[0]._tr.get_or_add_trPr()
                if header_pr.find(qn("w:tblHeader")) is None:
                    header = OxmlElement("w:tblHeader")
                    header.set(qn("w:val"), "true")
                    header_pr.append(header)
    properties = doc.core_properties
    properties.title = "Data Review, Smart Grid, Reconciliation, and Reporting"
    properties.subject = "Current spreadsheet-style Oracle Planning data review, validation, reconciliation, and reporting guide"
    properties.author = "BISP Solutions"
    properties.keywords = "Oracle EPM, Planning, Data Review, Smart Grid, reconciliation, validation, Excel reports, read-only"
    properties.comments = "Documents the current React, FastAPI, PostgreSQL evidence, Oracle data-slice, and Excel reporting implementation as of 3 September 2026; retired UI excluded."


def build_document() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    assets = create_assets()
    doc = Document()
    configure_styles(doc)
    patch_list_numbering(doc)
    for section in doc.sections:
        configure_page(section, first_page=True)
        configure_header_footer(section)

    add_cover(doc)
    add_page_break(doc)

    doc.add_paragraph("HOW TO USE THIS VOLUME", style="Kicker")
    doc.add_paragraph("Begin with the business question", style="Heading 1")
    doc.add_paragraph(
        "This guide follows the user from choosing a Planning cube to receiving a live grid, a validation verdict, reconciliation evidence, or an Excel workbook. It explains what each control means, what is stored, and when Oracle configuration or compatibility still matters.",
        style="Lead",
    )
    add_table(
        doc,
        ["If you are...", "Start with", "Primary goal"],
        [
            ["A planner or business user", "Chapters 2-5", "Build a live grid, inspect values, and run the appropriate validation."],
            ["An FP&A reviewer", "Chapters 5-7", "Understand exceptions, reconcile cubes, and decide whether evidence is acceptable."],
            ["A Service Administrator", "Chapters 7, 10, and 11", "Configure task context, understand compatibility, and support users."],
            ["An Oracle EPM consultant", "Chapters 2, 6, 8, and 10", "Confirm dimensional alignment, report definitions, APIs, and Oracle-side prerequisites."],
        ],
        [2350, 1800, 5210],
    )
    add_heading(doc, "Contents")
    add_table(
        doc,
        ["Part", "Chapters", "What it explains"],
        [
            ["Foundation", "1-2", "The read-only model, access, live discovery, and dimension placement."],
            ["Smart Grid", "3-4", "Member selection, loading, spreadsheet navigation, refresh, and size controls."],
            ["Validation", "5-7", "Quality checks, source-target reconciliation, and task-linked evidence."],
            ["Outputs", "8", "Grid exports, validation evidence, registered reports, and monitored report generation."],
            ["Assistance", "9", "How the EPM Assistant discovers, reviews, compares, and hands off safely."],
            ["Support", "10-12", "Compatibility, troubleshooting, operating practices, boundaries, and glossary."],
        ],
        [1900, 1550, 5910],
    )
    add_chapter(doc, 1, "Understand the read-only data model", "The platform provides a spreadsheet-style view of Oracle Planning data without becoming a second data-entry application.")
    add_figure(doc, assets["journey"], "Figure 7.1 - From a business question to read-only evidence.", "Four-step flow from choosing a live cube through dimension and member selection to Smart Grid review, validation, reconciliation, or Excel output, with a read-only guarantee.", width=6.35)
    add_heading(doc, "What Data Review does")
    add_bullets(doc, [
        "Discovers Planning cubes visible to the configured Oracle identity.",
        "Builds an ad-hoc data slice from POV, row, and column member selections; a Planning form or registered report is not required.",
        "Exports the selected slice through Oracle's supported Planning data-slice endpoint and normalizes the response into a spreadsheet grid.",
        "Runs optional data-quality checks or compares the same layout between a source cube and a target cube.",
        "Exports the live grid or its validation evidence to Excel.",
        "Links summary validation evidence to an assigned Planning responsibility when Data Review was opened from My Work.",
    ])
    add_heading(doc, "What it deliberately does not do")
    add_bullets(doc, [
        "It does not edit, submit, calculate, spread, allocate, lock, or write cell values back to Oracle Planning.",
        "It is not a replacement for Oracle Smart View, Planning forms, ad-hoc writeback, or application design.",
        "It does not decide whether a business value is correct; it exposes selected facts, configured exceptions, and differences for an authorized person to assess.",
        "It does not bypass Oracle data security. Oracle controls which cubes, dimensions, members, and values the configured identity can access.",
    ])
    add_callout(doc, "Privacy-oriented evidence", "Ad-hoc cell values are displayed and exported on request, but task-validation records store the selected layout, criteria, counts, verdict, user, and timestamps - not the Planning cell values themselves.", fill=GOLD_LIGHT, accent=GOLD)

    add_chapter(doc, 2, "Choose the cube and place dimensions", "The modern workspace obtains its cube, dimension, and member choices from Oracle whenever the connected release exposes the required metadata.")
    add_figure(doc, assets["layout"], "Figure 7.2 - POV, row, and column placement.", "Example dimension layout with fixed Scenario, Version, Year, and Entity in POV, Period across columns, and Account and Product down rows.", width=6.35)
    add_heading(doc, "Step 1 - Select a live cube")
    add_bullets(doc, [
        "Open Data Review and choose Live cube / plan type. The list is fetched from Oracle; it is not derived from registered reports.",
        "Select Refresh from Oracle when the application or its plan types have changed.",
        "In Oracle Cloud mode, an empty cube list is treated as a discovery or access problem instead of silently showing stale registrations.",
        "For an older on-premises release that cannot list plan types, Advanced compatibility fallback can try an exact cube name. Registered Data Review definitions may also contribute compatible cube names on-premises.",
    ])
    add_heading(doc, "Step 2 - Let Smart layout make the first arrangement")
    add_para(doc, "After dimensions load, Smart layout looks for Period and Account. Period becomes the initial column dimension, Account becomes the initial row dimension, and the remaining dimensions begin in POV. If those names are not available, the workspace uses the first suitable dimensions returned by Oracle. This is a starting arrangement, not a semantic claim about the application design.")
    add_heading(doc, "Step 3 - Place every dimension exactly once")
    add_table(
        doc,
        ["Location", "Selection rule", "Typical use"],
        [
            ["Point of view", "Exactly one member per dimension", "Scenario, Version, Year, Entity, Currency, View, or other fixed context."],
            ["Rows", "One or more members per dimension", "Accounts, products, entities, projects, cost centers, or other vertical analysis."],
            ["Columns", "One or more members per dimension", "Periods and another compact analysis dimension across the sheet."],
        ],
        [2200, 2500, 4660],
    )
    add_callout(doc, "Validation before load", "A dimension cannot be assigned twice. Rows and columns are required. Every chosen dimension needs at least one member, and POV dimensions keep only one member.", fill=BLUE_LIGHT, accent=BLUE)

    add_chapter(doc, 3, "Select members with minimal typing", "The member picker is designed for mouse-first use while preserving exact-name entry as a compatibility fallback.")
    add_heading(doc, "Browse live Oracle members")
    add_numbered(doc, [
        "Choose a dimension or use the placement already proposed by Smart layout.",
        "Select Browse. The first bounded page of live Oracle members appears with name, alias when available, hierarchy path or parent information, and total matches.",
        "Select one member for POV or multiple members for rows and columns. Selected members appear as removable tokens.",
        "Use Load more for large hierarchies. Search is optional and matches member name, alias, and path.",
        "Review every selected token before loading because Oracle evaluates exact dimension and member names.",
    ])
    add_heading(doc, "Current search behavior")
    add_table(
        doc,
        ["Behavior", "Current implementation"],
        [
            ["Initial page", "Up to 40 matching members."],
            ["Load more", "Up to 100 additional members per request; the service limit is 100 per request."],
            ["Search delay", "The browser waits briefly while typing before requesting results, reducing unnecessary calls."],
            ["Server cache", "Complete member lists are cached for five minutes, with a bounded cache of 32 cube-dimension entries."],
            ["Exact-name fallback", "If member discovery is unavailable, an exact member expression can still be entered and Oracle validates it when the slice is loaded."],
        ],
        [2500, 6860],
    )
    add_heading(doc, "Saved layout behavior")
    add_para(doc, "After a successful review, the browser saves the cube layout in local storage using the current host and cube name. If dimension discovery later becomes unavailable, the last successful layout can be restored on that browser. This is a convenience fallback, not a shared server-side report definition, and it does not follow the user to another browser or host.")
    add_callout(doc, "Prefer live choices", "Use exact names only when the connected Oracle release cannot expose metadata. Live lists reduce spelling mistakes and reveal application changes before the data request is sent.", fill=GOLD_LIGHT, accent=GOLD)

    add_chapter(doc, 4, "Work with the Smart Grid", "Once Oracle returns the selected slice, the layout collapses and the spreadsheet-style review surface becomes the primary workspace.")
    add_figure(doc, assets["grid"], "Figure 7.3 - Smart Grid anatomy.", "Spreadsheet-style Planning grid showing POV cards, selected-cell formula strip, row and column headers, values, a missing cell, and navigation controls.", width=6.35)
    add_heading(doc, "What appears above the grid")
    add_bullets(doc, [
        "The live cube name and the number of returned rows and columns.",
        "A read-only badge, Export Excel, Refresh, and Edit layout controls.",
        "POV cards showing each fixed dimension and member.",
        "Metrics for rows, columns, data cells, and missing cells.",
        "A row-header search, page-size selector, and formula-style strip that shows the selected cell's complete row and column intersection and value.",
    ])
    add_heading(doc, "How to navigate")
    add_table(
        doc,
        ["Need", "Action", "Important detail"],
        [
            ["Inspect a cell", "Click it or focus it with the keyboard.", "The formula strip shows its row intersection, column intersection, and displayed value."],
            ["Find a row", "Search row members.", "Filtering is performed in the browser against returned row headers; it does not issue another Oracle query."],
            ["See more rows", "Choose 25, 50, or 100 rows per page and use Previous or Next.", "Paging changes only the current browser view."],
            ["Get current values", "Select Refresh.", "The same reviewed layout is exported from Oracle again."],
            ["Change the slice", "Select Edit layout.", "Adjust cube, placements, or member selections, then load a new live grid."],
        ],
        [1900, 3150, 4310],
    )
    add_heading(doc, "Size and suppression")
    add_para(doc, "Before calling Oracle, the backend estimates the Cartesian product of row and column member selections. Requests above 250,000 intersections are rejected so one browser action cannot create an unbounded query. Oracle is asked to suppress missing blocks, so the returned row count can be smaller than the estimate.")
    add_callout(doc, "Missing versus zero", "A missing value has no Planning data and is displayed as a dash. Zero is a numeric value. The quality checker treats them differently and never assumes that zero is missing.", fill=TEAL_LIGHT, accent=TEAL)

    add_chapter(doc, 5, "Run data-quality checks", "Quality checks apply clear, bounded rules to every cell in the current live slice and produce Pass, Warning, or Fail.")
    add_figure(doc, assets["quality"], "Figure 7.4 - Current quality checks and verdicts.", "Four quality checks - missing, zero, minimum or maximum, and non-numeric - leading to Pass, Warning, or Fail.", width=6.35)
    add_heading(doc, "Current controls")
    add_table(
        doc,
        ["Check", "Default", "Outcome"],
        [
            ["Missing values", "Enabled", "Each missing cell is a failure."],
            ["Zero values", "Disabled", "When enabled, each zero is a warning."],
            ["Minimum allowed", "No minimum", "A numeric value below the entered minimum is a failure."],
            ["Maximum allowed", "No maximum", "A numeric value above the entered maximum is a failure."],
            ["Non-numeric value", "Applies when a numeric rule is active", "A value that cannot be interpreted as a number is a failure."],
        ],
        [2500, 2200, 4660],
    )
    add_heading(doc, "How the verdict is calculated")
    add_bullets(doc, [
        "Fail: at least one error exists, including missing data when enabled, out-of-range data, or non-numeric data under a numeric rule.",
        "Warning: there are no errors, but at least one enabled zero-value warning exists.",
        "Pass: every returned cell passed every enabled check.",
        "The summary counts every checked cell and every issue. The browser and workbook return a bounded issue list; the current UI requests up to 500 issue details, while the API accepts a maximum of 2,000.",
    ])
    add_heading(doc, "Review exception evidence")
    add_para(doc, "The result lists severity, check, row intersection, column intersection, and value. Selecting an issue opens an inspector with the POV and complete intersection, so a reviewer can locate the same cell in Oracle. Export evidence creates a workbook with Validation Summary and Exceptions worksheets.")
    add_callout(doc, "A Pass is scoped", "Pass means the selected cells passed the selected rules at that moment. It does not prove that an unselected account, entity, period, scenario, or cube is correct.", fill=GOLD_LIGHT, accent=GOLD)

    add_chapter(doc, 6, "Reconcile a source cube with a target cube", "Comparison asks whether corresponding cells in two live cube slices agree within an allowed business tolerance.")
    add_figure(doc, assets["reconciliation"], "Figure 7.5 - Source-to-target comparison behavior.", "Two matching live cube slices converging on decimal comparison, with examples of transport noise, business tolerance, genuine difference, and missing values.", width=6.35)
    add_heading(doc, "How to run a comparison")
    add_numbered(doc, [
        "Build and load the source cube slice first.",
        "Choose Compare target cube under Business validation.",
        "Choose or enter the target cube. The platform reuses the same POV, row dimensions, column dimensions, members, and order.",
        "Enter a non-negative allowed numeric difference. Use zero when the cubes should agree exactly at business precision.",
        "Select Compare live data and review the Compared, Matched, and Different totals.",
        "Open a difference to see the row intersection, column intersection, source value, target value, difference, and both POVs.",
    ])
    add_heading(doc, "Rules that prevent false conclusions")
    add_bullets(doc, [
        "Source and target must return the same row dimensions, column dimensions, column headers, number of rows, and row-member order. A layout mismatch stops the comparison instead of comparing unrelated cells.",
        "Both values are converted to decimal numbers. Non-numeric comparison data stops the comparison with a clear error.",
        "Two missing values match. One missing value and one numeric value differ.",
        "Difference is calculated as source minus target.",
        "Values match when the absolute difference is within the selected tolerance.",
        "A very small relative epsilon also ignores numeric transport noise such as 1,070.0000000000002 versus 1,070. This does not increase the selected business tolerance for genuine differences.",
    ])
    add_heading(doc, "Bounded difference evidence")
    add_para(doc, "The browser requests up to 500 mismatch details and displays differences rather than repeating every matching cell. Compared and Matched totals always reflect the complete returned slice. The export uses the same bounded request, so its Cell Comparison worksheet contains the returned comparison evidence, not an unlimited dump of every possible intersection.")
    add_callout(doc, "Interpretation belongs to the business", "A difference can be expected because of timing, mapping, aggregation, currency translation, or target logic. The platform identifies the intersection; the process owner decides whether the cause is acceptable.", fill=GOLD_LIGHT, accent=GOLD)

    add_chapter(doc, 7, "Complete an assigned Data Review task", "When Data Review is opened from My Work, the live check becomes governed lifecycle evidence rather than an isolated analysis.")
    add_figure(doc, assets["task"], "Figure 7.6 - Task-linked validation evidence.", "Five-step task flow from My Work through restored context, live validation, summary evidence, and controlled completion, with Pass, Warning, and Fail governance.", width=6.35)
    add_heading(doc, "What the assignment can carry")
    add_bullets(doc, [
        "The source cube and exact POV, row, and column layout.",
        "The required validation method: quality or comparison.",
        "Prefilled quality rules, target cube, or tolerance for the user to review.",
        "Cycle, stage, task title, description, owner, due context, and prerequisites from My Work.",
    ])
    add_heading(doc, "What the backend enforces")
    add_table(
        doc,
        ["Control", "Current behavior"],
        [
            ["Assignment", "Only the named user or a user holding the assigned platform role can use the task-linked context."],
            ["Prerequisites", "Unfinished dependencies block validation for that task."],
            ["Source selection", "When a source slice was configured, task evidence is rejected if the reviewed selection does not match it exactly."],
            ["Method", "A task configured for quality cannot record comparison evidence, and vice versa."],
            ["Completion", "Pass permits completion. Warning permits completion only after acknowledgement. Fail blocks completion."],
            ["Latest evidence", "Only the latest warning can be acknowledged; rerunning creates a new latest result."],
        ],
        [2500, 6860],
    )
    add_heading(doc, "What is stored")
    add_para(doc, "PostgreSQL stores the task ID, validation type, status, source and optional target cube, selected layout, criteria, checked and matched counts, exception and warning counts, performer, time, and optional warning acknowledgement. It intentionally does not store the reviewed Planning cell values.")
    add_callout(doc, "Do not complete around a failure", "The task service rejects manual completion when the latest validation failed or when a warning has not been acknowledged. Correct the data or selection, rerun, and use the newest evidence.", fill=RED_LIGHT, accent=RED)

    add_chapter(doc, 8, "Export grids, evidence, and reports", "Excel output supports analysis and audit without turning the web grid into a permanent copy of Planning data.")
    add_figure(doc, assets["outputs"], "Figure 7.7 - Current Excel output paths.", "Four Excel paths for Smart Grid data, quality evidence, comparison evidence, and monitored Report Generation.", width=6.35)
    add_heading(doc, "Three exports inside Data Review")
    add_table(
        doc,
        ["Button", "Workbook content", "Execution behavior"],
        [
            ["Export Excel", "Formatted Report sheet plus Report Metadata with title, application, data source, timestamp, axes, counts, and POV.", "The backend re-reads the selected live slice and returns the workbook directly."],
            ["Export evidence after quality checks", "Validation Summary plus Exceptions, including rules, counts, status, POV, and bounded issue details.", "The backend reruns the live quality validation before creating the workbook."],
            ["Export evidence after comparison", "Comparison Summary plus Cell Comparison for returned differences or cells, tolerance, and source/target values.", "The backend reruns the live comparison with the current request before creating the workbook."],
        ],
        [2150, 4350, 2860],
    )
    add_callout(doc, "Point-in-time behavior", "Because export re-reads Oracle, a workbook can differ from an older grid still visible in the browser if Planning data changed between Load, Refresh, and Export.", fill=GOLD_LIGHT, accent=GOLD)
    add_heading(doc, "Report Generation workspace")
    add_para(doc, "Reports is a separate read-only operation for a repeatable business output. An authorized user can choose a registered report definition or, when the Oracle release supports it, inspect an exact Planning form name or ID. The screen preflights rows, columns, POV dimensions, and available page members before asking for confirmation.")
    add_numbered(doc, [
        "Choose Registered report or Planning form.",
        "Inspect the layout. For a live form, Oracle must expose the form layout and export APIs to the configured identity.",
        "Confirm the workbook title and every required POV member.",
        "Review the source, cube, row and column dimensions, and POV; then select the explicit read-only confirmation.",
        "Generate the report. The request enters the monitored execution workflow, and the finished workbook is retained as an execution artifact in Jobs & Activity.",
    ])
    add_heading(doc, "Registered report definitions")
    add_para(doc, "When direct form inspection is unavailable or a reusable governed layout is preferred, Register report records a name, workbook title, cube, optional fixed POV, row dimensions and members, and column dimensions and members in the current report catalog file. The definition describes a data slice; it does not create or alter an Oracle Planning form.")
    add_callout(doc, "Choose the simplest output", "Use Smart Grid export for the intersection you just explored, validation evidence for a controlled check, and Report Generation for a repeatable named business workbook.", fill=TEAL_LIGHT, accent=TEAL)

    add_chapter(doc, 9, "Use the EPM Assistant with Data Review", "The assistant provides a conversational entry point, but it uses the same read-only services and cannot bypass dimensions, permissions, or validation rules.")
    add_heading(doc, "Current assistant capabilities")
    add_bullets(doc, [
        "List Planning cubes available through the Data Review service.",
        "List the dimensions exposed for a selected cube.",
        "Search a cube dimension for live members using a bounded result page.",
        "Review one exact ad-hoc slice when cube, POV, row members, and column members are known.",
        "Compare two exact slices within a specified tolerance.",
        "Carry a selected cube or complete validated layout into the modern Data Review workspace so the user can continue visually.",
    ])
    add_heading(doc, "What the assistant does not do")
    add_bullets(doc, [
        "It cannot write Planning cell values or turn the Smart Grid into data entry.",
        "It cannot invent cube, dimension, or member names when live discovery or an exact user-provided value is required.",
        "It does not currently replace the task-linked quality-check controls and warning acknowledgement in the Data Review workspace.",
        "It does not generate a formal Reports workbook directly from free text; report requests hand off to the governed Reports workspace.",
        "It cannot expose data outside the signed-in platform role and configured Oracle identity's access.",
    ])
    add_heading(doc, "Example conversation")
    add_table(
        doc,
        ["User", "Assistant behavior"],
        [
            ["Show Forecast revenue for Plan1, FY27, Jan to Mar.", "Find the live cube, identify required dimensions, and ask only for missing POV or member choices."],
            ["Use Account on rows and Period on columns.", "Preserve the chosen cube and place the dimensions in the requested axes."],
            ["Compare the same slice with Reporting at tolerance 1.", "Run the read-only comparison when both slice definitions are complete, then summarize differences and offer a visual Data Review handoff."],
        ],
        [3150, 6210],
    )
    add_chapter(doc, 10, "Handle Oracle compatibility and errors", "Cloud and on-premises Planning releases do not expose identical discovery and form APIs, so the interface separates safe fallbacks from false success.")
    add_heading(doc, "Common situations")
    add_table(
        doc,
        ["Message or symptom", "Likely meaning", "What to do"],
        [
            ["No live cubes available", "Oracle returned no accessible plan types or discovery failed.", "Confirm application selection and Oracle access; use Refresh from Oracle. On older on-premises releases, use the advanced exact-cube fallback when appropriate."],
            ["No dimensions appear", "The selected release does not expose the plan-type dimension API, or access is insufficient.", "Retry Oracle. If the cube is correct, use exact names or a last successful browser layout; the live slice endpoint remains the final validator."],
            ["Member search unavailable", "Oracle does not expose member discovery for that release or dimension.", "Enter an exact member name only when it is known and let the live data request validate it."],
            ["Dimension or member not found", "The name is wrong, belongs to another cube, changed in Oracle, or is inaccessible.", "Refresh metadata and reselect; do not keep retrying a stale exact name."],
            ["Requested slice is too large", "The row-column Cartesian product exceeds 250,000 intersections.", "Reduce row or column members or move a suitable dimension to a fixed POV."],
            ["Source and target layouts differ", "The cubes did not return matching headers or dimensional order.", "Align metadata, member selections, mappings, and security before interpreting values."],
            ["Authentication failed only here", "Data Review uses live Planning metadata and data APIs that may expose an application, domain, credential, or role problem not exercised by another screen.", "Verify the selected Planning application, base URL, username, password, identity-domain requirements, and Oracle data access."],
        ],
        [2700, 3250, 3410],
    )
    add_heading(doc, "Safe troubleshooting order")
    add_numbered(doc, [
        "Confirm the platform shows the intended connected Planning application.",
        "Confirm the current user has Data Review permission and, for task-linked work, owns the assignment directly or through a role.",
        "Refresh the cube list and choose the cube again.",
        "Load dimensions, then browse one simple member from each dimension.",
        "Test a very small slice before expanding rows or columns.",
        "If metadata discovery is unavailable, use an exact-name fallback only with names verified in Oracle.",
        "For comparison, prove each cube slice loads independently before comparing them.",
    ])
    add_callout(doc, "Do not hide Oracle errors", "A 400 or 404 can mean an unsupported endpoint, but it can also mean an invalid application, cube, dimension, member, or permission. Confirm the context before choosing a compatibility fallback.", fill=RED_LIGHT, accent=RED)

    add_chapter(doc, 11, "Use a repeatable review practice", "A disciplined selection and evidence routine is more valuable than a large grid with unclear business meaning.")
    add_heading(doc, "Before loading")
    add_bullets(doc, [
        "Write the business question in one sentence: what measure, organization, scenario, version, year, and period should be reviewed?",
        "Choose the smallest useful slice and put fixed context in POV.",
        "Use live cube, dimension, and member choices whenever available.",
        "Check that every dimension is placed exactly once and that row and column order matches the intended analysis.",
        "For comparison, confirm source and target dimensionality and business meaning are genuinely aligned.",
    ])
    add_heading(doc, "After loading")
    add_bullets(doc, [
        "Read the POV before interpreting a number.",
        "Check returned row, column, cell, and missing counts for reasonableness.",
        "Inspect several known intersections and use the selected-cell strip to confirm full context.",
        "Choose quality checks for completeness or thresholds; choose reconciliation for corresponding source-target values.",
        "Investigate exceptions in Oracle or with the process owner. Do not change tolerance merely to produce a Pass.",
        "Export the appropriate workbook when evidence must be shared, and label it with the cycle or business period outside the platform if necessary.",
    ])
    add_heading(doc, "Example monthly review")
    add_table(
        doc,
        ["Step", "Example action", "Expected evidence"],
        [
            ["1. Actuals completeness", "Plan1; Actual, Working, FY27, Sales East; Accounts on rows; Jan-Feb on columns; missing enabled.", "No missing actual intersections, or a bounded exception list."],
            ["2. Forecast reasonableness", "Forecast, Working, FY27; Mar-Dec; enable minimum zero for a non-negative driver slice where that business rule is valid.", "Pass or explained out-of-range intersections."],
            ["3. Reporting reconciliation", "Compare the approved Plan1 slice with Reporting after the publish step.", "Compared and matched totals plus exact differences within the selected tolerance."],
            ["4. Governance", "Open from My Work when the cycle includes a Data Review responsibility.", "Latest Pass or acknowledged Warning linked to the task and approval context."],
            ["5. Distribution", "Export validation evidence or generate the named report.", "Point-in-time Excel workbook and, for Report Generation, retained Jobs & Activity evidence."],
        ],
        [1600, 4710, 3050],
    )
    add_callout(doc, "Keep the question stable", "If the cube, POV, rows, columns, rules, or tolerance changes, treat the result as a new review. Record or communicate the changed scope before comparing conclusions.", fill=GOLD_LIGHT, accent=GOLD)

    add_chapter(doc, 12, "Know the current boundaries and terms", "The spreadsheet-style experience is intentionally bounded so it remains understandable, secure, and supportable.")
    add_heading(doc, "Implemented now")
    add_bullets(doc, [
        "Modern role-aware Data Review workspace with live cube discovery, dimension placement, Smart layout, member browse/search, exact-name fallback, and local layout restoration.",
        "Ad-hoc Oracle data-slice loading without requiring a form or registered report, with a 250,000-intersection request limit.",
        "Spreadsheet-style read-only grid with POV, metrics, row search, 25/50/100-row paging, selected-cell context, refresh, and Excel export.",
        "Missing, zero, minimum, maximum, and non-numeric quality checks with bounded exception evidence.",
        "Source-to-target cube comparison with dimensional alignment checks, decimal tolerance, technical transport-noise protection, and bounded differences.",
        "Task-linked summary validation evidence, warning acknowledgement, and controlled completion.",
        "Registered or live-form Report Generation with preflight, explicit confirmation, monitored execution, and downloadable Excel artifacts.",
        "EPM Assistant cube, dimension, member, slice-review, comparison, and modern-workspace handoff capabilities.",
    ])
    add_heading(doc, "Not implemented in the current Smart Grid")
    add_bullets(doc, [
        "Cell editing, writeback, spreading, comments, supporting detail, ad-hoc calculations, drill-through, pivoting, zoom-in hierarchy expansion, or Smart View parity.",
        "Automatic business validation rules inferred from the cube. Users or cycle designers must choose suitable checks and tolerances.",
        "Unlimited grids or unlimited exception downloads.",
        "Cross-browser or cross-device synchronization of ad-hoc saved layouts; browser restoration is local to the host and cube.",
        "A guarantee that every Oracle release supports cube, dimension, member, or Planning-form discovery through the same endpoints.",
        "Automatic correction of metadata, mappings, security, data, or reconciliation differences.",
    ])
    add_heading(doc, "Glossary")
    add_table(
        doc,
        ["Term", "Plain-language meaning"],
        [
            ["Smart Grid", "The platform's spreadsheet-style, read-only display for one live Planning data slice; it is not Oracle Smart View."],
            ["Cube / plan type", "A multidimensional Planning data store such as an input, workforce, or reporting cube."],
            ["Dimension", "A business perspective used to organize data, such as Account, Entity, Product, Period, Scenario, Version, or Year."],
            ["Member", "One named item within a dimension, such as FY27, Forecast, Sales East, Jan, or Revenue."],
            ["POV", "Point of view: fixed one-member context for dimensions not varied across rows or columns."],
            ["Data slice", "The bounded set of intersections created from the selected POV, row members, and column members."],
            ["Missing", "An intersection with no returned Planning value; different from numeric zero."],
            ["Tolerance", "The largest accepted absolute business difference between corresponding source and target values."],
            ["Technical epsilon", "A very small automatic allowance that ignores numeric transport noise without masking meaningful business differences."],
            ["Validation evidence", "A retained summary of selection, criteria, verdict, counts, performer, and time for a task-linked review."],
            ["Registered report", "A reusable platform data-slice definition used to generate a named Excel workbook; it is not a newly created Oracle form."],
            ["Preflight", "A read-only check that resolves and displays a report's layout and required POV before generation."],
        ],
        [2550, 6810],
    )
    add_callout(doc, "End of Document 07", "Use this volume whenever a user needs to inspect numbers, prove data quality, reconcile cubes, or produce a controlled Excel output. Document 08 continues with the EPM Assistant and AI Agent: conversations, live tools, artifact recommendations, governed inputs, approvals, execution, safety, and limitations.", fill=TEAL_LIGHT, accent=TEAL)

    _finish_document(doc)
    doc.save(OUTPUT_FILE)
    return OUTPUT_FILE


if __name__ == "__main__":
    print(build_document())
