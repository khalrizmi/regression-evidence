"""Evidence document: layout shared with Preview Doc, and the .docx writer."""
from __future__ import annotations

import math
import re
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, Twips
from PIL import Image

MAX_WIDTH_PX = 800
PLATFORM_LABEL = {"android": "Android", "ios": "iOS"}
UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|]+')


MARGIN_CM = 2.5  # same as the page padding of Preview Doc (app/static/style.css .page)

# SIT layout, measured from the team's SIT evidence document: US Letter, one 2-column table per TC.
SIT_PAGE = (Twips(12240), Twips(15840))
SIT_MARGINS = (Twips(864), Twips(792))  # left/right, top/bottom
SIT_COLUMNS = (Twips(1350), Twips(9162))  # label, value: together the full text width
SIT_ROWS = ("No. TC", "Scenario", "Category", "Steps", "Expected Result", "Screen Capture")
OUTPUT_PREFIX = {"regression": "Regression", "sit": "SIT"}


# ---- fitting every picture of a TC onto the page of its title ----------------------------------
# Every TC starts on a new page, so the room left for its pictures is known before writing: the page
# text height minus what sits above the pictures. Picture rows are laid out explicitly (line breaks),
# and the picture width is the largest that fits that room, capped at the usual width so TCs with
# one or two pictures look as before. Heights are estimates of Word's layout, hence SAFETY.
GAP_CM = 0.2  # a space between two pictures on a row, generous
ROW_PAD_CM = 0.15  # a picture line is slightly taller than the picture (font descent)
SAFETY_CM = 0.8
MIN_WIDTH_CM = 2.5  # below this, continue on a next page instead of shrinking further
BREAK_REMNANT_CM = 0.1  # the page-break paragraph's mark, which lands on the next page

REG_MAX_WIDTH_CM = 7.6
REG_TEXT_WIDTH_CM = 21.59 - 2 * MARGIN_CM
REG_TEXT_HEIGHT_CM = 27.94 - 2 * MARGIN_CM
REG_H1_CM, REG_H1_CHARS = 1.55, 55  # Heading 1: 24 pt before + one 14 pt line at 1.15
REG_H2_CM, REG_H2_LINE_CM, REG_H2_CHARS = 1.0, 0.65, 60  # Heading 2: 10 pt before + 13 pt lines

SIT_MAX_WIDTH_CM = 7.4
SIT_TEXT_WIDTH_CM = 16.16 - 0.38  # value column minus the cell's left/right margins
SIT_TEXT_HEIGHT_CM = 27.94 - 2 * 1.397
SIT_LINE_CM = 0.42  # Arial 9 pt at 1.15
SIT_PARA_AFTER_CM = 0.035  # 1 pt after each value paragraph
SIT_LABEL_AFTER_CM = 0.35  # 10 pt after each label paragraph
SIT_CHARS = 85  # characters per line in the value column, on the low side
SIT_LABEL_LINES = {"Expected Result": 2, "Screen Capture": 2}  # labels that wrap in the 2.4 cm column


def _rows(ratios: list[float], cols: int) -> list[list[float]]:
    return [ratios[i:i + cols] for i in range(0, len(ratios), cols)]


def best_fit(ratios: list[float], width: float, height: float, max_width: float) -> tuple[int, float]:
    """(columns, picture width cm) giving the largest pictures that fit width x height.

    ratios are height/width per picture, in order; a row is as tall as its tallest picture.
    """
    best = (1, 0.0)
    for cols in range(1, len(ratios) + 1):
        rows = _rows(ratios, cols)
        by_width = (width - GAP_CM * (cols - 1)) / cols
        by_height = (height - ROW_PAD_CM * len(rows)) / sum(max(r) for r in rows)
        w = math.floor(min(max_width, by_width, by_height) * 100) / 100
        if w > best[1]:  # ties keep fewer columns: a squarer grid
            best = (cols, w)
    return best


def plan_blocks(ratios: list[float], first_height: float, next_height: float, width: float,
                max_width: float) -> list[dict]:
    """Split the pictures into one block per page: [{count, cols, width_cm}].

    Normally one block. Only when fitting would make the pictures narrower than MIN_WIDTH_CM do the
    rest go to next page(s). A first block with count 0 means not even one picture fits under the
    TC's text at the minimum width.
    """
    greedy = _plan_greedy(ratios, first_height, next_height, width, max_width)
    return _balanced(ratios, greedy, first_height, next_height, width, max_width) or greedy


def _balanced(ratios, greedy, first_height, next_height, width, max_width) -> list[dict] | None:
    """Spread the pictures evenly over the pages the greedy plan needs (16 -> 8 + 8, not 15 + 1),
    when every page still fits at the minimum width."""
    pages = [b for b in greedy if b["count"]]
    if len(pages) < 2:
        return None
    n, p = len(ratios), len(pages)
    counts = [n // p + (1 if i < n % p else 0) for i in range(p)]
    out, start = [b for b in greedy if not b["count"]], 0
    for i, count in enumerate(counts):
        height = first_height if not out and i == 0 else next_height
        cols, w = best_fit(ratios[start:start + count], width, height, max_width)
        if w < MIN_WIDTH_CM:
            return None
        out.append({"count": count, "cols": cols, "width_cm": w})
        start += count
    return out


def _plan_greedy(ratios, first_height, next_height, width, max_width) -> list[dict]:
    blocks, rest, height = [], list(ratios), first_height
    while rest:
        cols, w = best_fit(rest, width, height, max_width)
        if w >= MIN_WIDTH_CM:
            blocks.append({"count": len(rest), "cols": cols, "width_cm": w})
            break
        k = len(rest) - 1
        while k and best_fit(rest[:k], width, height, max_width)[1] < MIN_WIDTH_CM:
            k -= 1
        if k == 0 and not blocks and next_height > height:
            blocks.append({"count": 0, "cols": 1, "width_cm": 0.0})
        else:
            k = max(k, 1)  # a single picture taller than a page: smaller than the minimum, still whole
            cols, w = best_fit(rest[:k], width, height, max_width)
            blocks.append({"count": k, "cols": cols, "width_cm": w})
            rest = rest[k:]
        height = next_height
    return blocks


def _lines_cm(text: str, chars: int, line_cm: float) -> float:
    return max(1, math.ceil(len(text) / chars)) * line_cm


def _sit_text_height(case: dict) -> float:
    """Estimated height of the SIT table above the Screen Capture row."""
    values = [[case["label"]], [case["title"]], [case["type"]], _lines(case["steps"]), _lines(case["expected"])]
    total = 0.0
    for label, lines in zip(SIT_ROWS, values):
        label_h = SIT_LABEL_LINES.get(label, 1) * SIT_LINE_CM + SIT_LABEL_AFTER_CM
        value_h = sum(_lines_cm(line, SIT_CHARS, SIT_LINE_CM) + SIT_PARA_AFTER_CM for line in (lines or [""]))
        total += max(label_h, value_h)
    return total


def _ratio(path: Path) -> float:
    with Image.open(path) as img:  # reads the header only
        return img.height / img.width


def _blocks(names: list[str], ratios: list[float], plan: list[dict]) -> list[dict]:
    out, i = [], 0
    for b in plan:
        out.append({**b, "images": names[i:i + b["count"]]})
        i += b["count"]
    return out


def doc_layout(session) -> list[dict]:
    headings = session.headings()
    labels = session.labels()
    sit = session.doc_type == "sit"
    modules: dict[str, list[dict]] = {}
    for c in session.active_cases():
        if c["status"] == "Skip" and not c["images"]:
            continue
        names = [n for n in c["images"] if (session.shots / n).is_file()]
        ratios = [_ratio(session.shots / n) for n in names]
        case = {
            "tc_no": c["tc_no"],
            "label": labels[c["tc_no"]],
            "heading": headings[c["tc_no"]],
            "title": c["title"],
            "type": c["type"],
            "steps": c["steps"],
            "expected": c["expected"],
            "status": c["status"],
            "images": names,
        }
        if sit:
            label_h = SIT_LABEL_LINES["Screen Capture"] * SIT_LINE_CM + SIT_LABEL_AFTER_CM
            first = SIT_TEXT_HEIGHT_CM - BREAK_REMNANT_CM - SAFETY_CM - _sit_text_height(case)
            cont = SIT_TEXT_HEIGHT_CM - BREAK_REMNANT_CM - SAFETY_CM - 2 * label_h
            plan = plan_blocks(ratios, first, cont, SIT_TEXT_WIDTH_CM, SIT_MAX_WIDTH_CM)
        else:
            h2 = REG_H2_CM + (_lines_cm(case["heading"], REG_H2_CHARS, REG_H2_LINE_CM) - REG_H2_LINE_CM)
            h1 = _lines_cm(c["module"], REG_H1_CHARS, REG_H1_CM) if c["module"] not in modules else 0.0
            room = REG_TEXT_HEIGHT_CM - BREAK_REMNANT_CM - SAFETY_CM
            plan = plan_blocks(ratios, room - h1 - h2, room - h2, REG_TEXT_WIDTH_CM, REG_MAX_WIDTH_CM)
        case["blocks"] = _blocks(names, ratios, plan)
        case["width_cm"] = next((b["width_cm"] for b in case["blocks"] if b["count"]), 0.0)
        modules.setdefault(c["module"], []).append(case)
    return [{"module": m, "cases": cases} for m, cases in modules.items()]


def pre_generate_warnings(session) -> dict:
    headings = session.headings()
    no_image, todo = [], []
    for c in session.active_cases():
        label = f"{c['module']} · {headings[c['tc_no']]}"
        if c["status"] != "Skip" and not c["images"]:
            no_image.append(label)
        if c["status"] == "Todo":
            todo.append(label)
    return {"no_image": no_image, "todo": todo}


def shrink(path: Path) -> BytesIO:
    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.width > MAX_WIDTH_PX:
            height = round(img.height * MAX_WIDTH_PX / img.width)
            img = img.resize((MAX_WIDTH_PX, height), Image.Resampling.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=85)
    buf.seek(0)
    return buf


def output_path(session) -> Path:
    d = session.data
    sheet = UNSAFE_FILENAME.sub("-", d["sheet"]).strip()
    build = UNSAFE_FILENAME.sub("-", d["build"]).strip()
    platform = PLATFORM_LABEL.get(d["platform"], d["platform"])
    prefix = OUTPUT_PREFIX[session.doc_type]
    return session.root / "out" / f"{prefix} _ {sheet} - {platform} {build}.docx"


def build_docx(session) -> Path:
    doc = build_sit(session) if session.doc_type == "sit" else build_regression(session)
    path = output_path(session)
    path.parent.mkdir(exist_ok=True)
    doc.save(path)
    return path


def _page_break(doc) -> None:
    """A page break whose paragraph mark, which Word puts on the next page, takes ~no room there."""
    para = doc.add_paragraph()
    para.paragraph_format.space_before = para.paragraph_format.space_after = Pt(0)
    para.paragraph_format.line_spacing = Pt(1)
    para.add_run().add_break(WD_BREAK.PAGE)


def _pictures(para, session, block: dict) -> None:
    """Pictures in rows of block['cols'], single spaced so a row is as tall as its pictures."""
    para.paragraph_format.space_before = para.paragraph_format.space_after = Pt(0)
    para.paragraph_format.line_spacing = 1.0
    run = para.add_run()
    width = Cm(block["width_cm"])
    for i, name in enumerate(block["images"]):
        if i and i % block["cols"] == 0:
            run.add_break()
        elif i:
            run.add_text(" ")
        shape = run.add_picture(shrink(session.shots / name), width=width)
        # Word reads missing wrap distances as 0; LibreOffice adds ~0.32 cm on every side, which
        # wraps a full row of pictures and pushes rows onto the next page.
        for side in ("distT", "distB", "distL", "distR"):
            shape._inline.set(side, "0")


def build_regression(session) -> Document:
    doc = Document()
    # python-docx's default 3.17 cm side margins leave 15.24 cm: too narrow for 2 x 7.6 cm pictures,
    # so Word would wrap the last picture onto the next page instead of beside the others.
    for section in doc.sections:
        section.left_margin = section.right_margin = Cm(MARGIN_CM)
        section.top_margin = section.bottom_margin = Cm(MARGIN_CM)
    first_page = True
    for module in doc_layout(session):
        for n, case in enumerate(module["cases"]):
            for b, block in enumerate(case["blocks"] or [{"count": 0, "images": []}]):
                if not first_page:
                    _page_break(doc)
                first_page = False
                if n == 0 and b == 0:
                    doc.add_heading(module["module"], level=1)
                if b == 0:
                    doc.add_heading(case["heading"], level=2)
                else:
                    cont = doc.add_paragraph(style="Heading 2")
                    cont.add_run(f"{case['heading']} (lanjutan)")
                    cont.paragraph_format.keep_with_next = True
                if block["images"]:
                    _pictures(doc.add_paragraph(), session, block)
    return doc


def _borders(table) -> None:
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        for key, value in (("val", "single"), ("sz", "6"), ("space", "0"), ("color", "000000")):
            el.set(qn(f"w:{key}"), value)
        borders.append(el)
    table._tbl.tblPr.append(borders)


def _fixed_width(table) -> None:
    """Word honours these; without them it sizes the columns to their content."""
    tbl_w = table._tbl.tblPr.find(qn("w:tblW"))
    tbl_w.set(qn("w:w"), str(sum(SIT_COLUMNS) // Twips(1)))
    tbl_w.set(qn("w:type"), "dxa")
    for col, width in zip(table._tbl.tblGrid.findall(qn("w:gridCol")), SIT_COLUMNS):
        col.set(qn("w:w"), str(width // Twips(1)))


def _fill(cell, lines: list[str], bold: bool = False, space_after: float = 1) -> None:
    """One paragraph per line, like the SIT template (numbered steps, '- ' expected results)."""
    for i, line in enumerate(lines or [""]):
        para = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        para.paragraph_format.space_after = Pt(space_after)
        para.paragraph_format.line_spacing = 1.15
        para.add_run(line).bold = bold


def _lines(text: str) -> list[str]:
    return [line.rstrip() for line in (text or "").splitlines() if line.strip()]


def _cant_split(table) -> None:
    for row in table.rows:
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))


def _sit_table(doc, rows: list[tuple[str, list[str] | None]]):
    table = doc.add_table(rows=len(rows), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _fixed_width(table)
    _borders(table)
    _cant_split(table)
    for row, (label, value) in zip(table.rows, rows):
        for cell, width in zip(row.cells, SIT_COLUMNS):
            cell.width = width
        _fill(row.cells[0], [label], bold=True, space_after=10)  # the template's row padding
        if value is not None:
            _fill(row.cells[1], value)
    return table


def build_sit(session) -> Document:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    normal.font.size = Pt(9)
    for section in doc.sections:
        section.page_width, section.page_height = SIT_PAGE
        section.left_margin = section.right_margin = SIT_MARGINS[0]
        section.top_margin = section.bottom_margin = SIT_MARGINS[1]
    cases = [case for module in doc_layout(session) for case in module["cases"]]
    first_page = True
    for case in cases:
        for b, block in enumerate(case["blocks"] or [{"count": 0, "images": []}]):
            if not first_page:
                _page_break(doc)
            first_page = False
            if b == 0:
                table = _sit_table(doc, list(zip(SIT_ROWS, [
                    [case["label"]], [case["title"]], [case["type"]], _lines(case["steps"]),
                    _lines(case["expected"]), None])))
            else:
                table = _sit_table(doc, [(SIT_ROWS[0], [f"{case['label']} (lanjutan)"]), (SIT_ROWS[-1], None)])
            para = table.rows[-1].cells[1].paragraphs[0]
            if block["images"]:
                _pictures(para, session, block)
            elif len(case["blocks"]) > 1:
                para.add_run("(lanjut di halaman berikutnya)")
    return doc
