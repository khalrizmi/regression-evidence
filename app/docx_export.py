"""Evidence document: layout shared with Preview Doc, and the .docx writer."""
from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.shared import Cm
from PIL import Image

MAX_WIDTH_PX = 800
PLATFORM_LABEL = {"android": "Android", "ios": "iOS"}
UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|]+')


def image_width_cm(count: int) -> float:
    return 7.6 if count <= 2 else 5.1


def doc_layout(session) -> list[dict]:
    headings = session.headings()
    modules: dict[str, list[dict]] = {}
    for c in session.active_cases():
        if c["status"] == "Skip" and not c["images"]:
            continue
        modules.setdefault(c["module"], []).append({
            "tc_no": c["tc_no"],
            "heading": headings[c["tc_no"]],
            "status": c["status"],
            "images": list(c["images"]),
            "width_cm": image_width_cm(len(c["images"])),
        })
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
    return session.root / "out" / f"Regression _ {sheet} - {platform} {build}.docx"


def build_docx(session) -> Path:
    doc = Document()
    layout = doc_layout(session)
    total = sum(len(m["cases"]) for m in layout)
    written = 0
    for module in layout:
        doc.add_heading(module["module"], level=1)
        for case in module["cases"]:
            doc.add_heading(case["heading"], level=2)
            files = [session.shots / n for n in case["images"] if (session.shots / n).exists()]
            if files:
                run = doc.add_paragraph().add_run()
                width = Cm(image_width_cm(len(files)))
                for i, f in enumerate(files):
                    if i:
                        run.add_text(" ")
                    run.add_picture(shrink(f), width=width)
            written += 1
            if written < total:
                doc.add_page_break()
    path = output_path(session)
    path.parent.mkdir(exist_ok=True)
    doc.save(path)
    return path
