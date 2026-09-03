"""
pdf_splitter.py
----------------
Chapter-wise PDF splitter.

Logic:
1. Scan every page's text. Wherever a line is *exactly* "Unit" (or the
   keyword you pass in, e.g. "Chapter"), that page is treated as the
   start of a new chapter.
2. Separately, try to read the Table of Contents page (a page that
   contains "Table of Content" / "Contents") to pull clean chapter
   titles like "Breeds of Animals" instead of messy in-page text.
3. Titles from the TOC are matched to the detected "Unit" pages in
   order. If TOC parsing fails, chapters just get named "Unit_1",
   "Unit_2", etc.
4. The PDF is split at those boundaries and each chapter is written
   out as its own PDF, plus everything before the first "Unit" is
   saved separately as "00_Front_Matter.pdf".
"""

import os
import re
from pypdf import PdfReader, PdfWriter


def _clean_filename(text: str, fallback: str) -> str:
    text = (text or "").strip()
    if not text:
        text = fallback
    text = re.sub(r"[^\w\s-]", "", text)      # drop punctuation
    text = re.sub(r"\s+", "_", text.strip())  # spaces -> underscores
    return text[:60] or fallback


def _find_unit_start_pages(reader: PdfReader, keyword: str = "Unit"):
    """
    Return the 0-indexed page numbers where a `keyword` heading (e.g. "Unit")
    is immediately followed by a number — that's a real chapter heading.

    A bare "Unit" line that is NOT followed by a number is treated as a
    false positive (e.g. a Table-of-Contents column header like
    "Unit / Content / Page" split across separate lines) and skipped.
    """
    starts = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.split("\n")]
        for j, line in enumerate(lines):
            if line.lower() != keyword.lower():
                continue
            # look at the next couple of lines for one that starts with digits
            for k in range(j + 1, min(j + 3, len(lines))):
                if re.match(r"^\d{1,3}", lines[k]):
                    starts.append(i)
                    break
            else:
                continue
            break  # only count the page once even if keyword repeats
    return starts


def _find_toc_titles(reader: PdfReader, expected_count: int):
    """
    Look through the first ~15 pages for a Table of Contents and pull
    out chapter titles in order. Returns a list of titles (may be
    shorter than expected_count if parsing fails).
    """
    toc_text = None
    for i in range(min(15, len(reader.pages))):
        text = reader.pages[i].extract_text() or ""
        if re.search(r"table\s+of\s+content", text, re.IGNORECASE):
            toc_text = text
            break
    if not toc_text:
        return []

    # Matches lines like "3. Care and Management of Animals   21"
    # Titles can wrap onto the next line in extracted text, so we
    # join everything after "Unit Content Page" and split on the
    # "N." pattern instead of relying on single lines.
    body = toc_text.split("Page", 1)[-1] if "Page" in toc_text else toc_text
    entries = re.split(r"(?m)^\s*(\d+)\.\s*", body)
    # entries looks like ['', '1', ' Introduction  1', '2', ' Breeds...  10', ...]
    titles = []
    for j in range(1, len(entries), 2):
        chunk = entries[j + 1] if j + 1 < len(entries) else ""
        chunk = chunk.replace("\n", " ")
        chunk = re.sub(r"\s+\d+\s*$", "", chunk).strip()  # strip trailing page number
        chunk = re.sub(r"\s{2,}", " ", chunk)
        if chunk:
            titles.append(chunk)
    return titles[:expected_count] if titles else []


def split_pdf_by_unit(input_path: str, output_dir: str, keyword: str = "Unit"):
    """
    Splits input_path into chapter PDFs inside output_dir.

    Returns a list of dicts: [{"title": ..., "filename": ..., "pages": "12-20"}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)
    reader = PdfReader(input_path)
    total_pages = len(reader.pages)

    starts = _find_unit_start_pages(reader, keyword=keyword)
    if not starts:
        raise ValueError(
            f'No "{keyword}" heading found in this PDF, so it can\'t be split that way.'
        )

    titles = _find_toc_titles(reader, expected_count=len(starts))

    boundaries = starts + [total_pages]
    results = []

    # Anything before the first Unit heading = front matter
    if starts[0] > 0:
        writer = PdfWriter()
        for p in range(0, starts[0]):
            writer.add_page(reader.pages[p])
        fname = "00_Front_Matter.pdf"
        with open(os.path.join(output_dir, fname), "wb") as f:
            writer.write(f)
        results.append({
            "title": "Front Matter",
            "filename": fname,
            "pages": f"1-{starts[0]}",
        })

    for idx, start in enumerate(starts):
        end = boundaries[idx + 1]  # exclusive
        writer = PdfWriter()
        for p in range(start, end):
            writer.add_page(reader.pages[p])

        chapter_num = idx + 1
        title = titles[idx] if idx < len(titles) else f"{keyword}_{chapter_num}"
        safe_title = _clean_filename(title, f"{keyword}_{chapter_num}")
        fname = f"{chapter_num:02d}_{safe_title}.pdf"

        with open(os.path.join(output_dir, fname), "wb") as f:
            writer.write(f)

        results.append({
            "title": title,
            "filename": fname,
            "pages": f"{start + 1}-{end}",
        })

    return results


def _find_lesson_start_pages(reader: PdfReader):
    """
    Find "lessons" — the numbered sub-topics inside each unit, like
    "1.1 Parasite and Parasitology", "1.2 Types of Parasites", "5.7 Pneumonia".

    Handles two layouts seen in real textbooks:
      a) "1.1 Some Title" on a single line
      b) "1.1" alone on its own line, with the title on the next line
         (happens when the PDF's text extraction breaks across a column/page)

    A heading's title is required to start with a capital letter and be more
    than a couple of characters — this filters out false positives like
    "4.1 fat percentage" showing up mid-sentence in body text.

    Returns a list of dicts: [{"page": 0-indexed page, "number": "1.1", "title": "..."}]
    """
    num_title_re = re.compile(r"^(\d{1,2}\.\d{1,2})\s+([A-Z]\S*(?:\s+\S+)*)$")
    num_only_re = re.compile(r"^(\d{1,2}\.\d{1,2})\s*$")

    found = []
    seen_numbers = set()
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for j, line in enumerate(lines):
            number, title = None, None

            m = num_title_re.match(line)
            if m and len(m.group(2)) >= 4:
                number, title = m.group(1), m.group(2)
            else:
                m = num_only_re.match(line)
                if m and j + 1 < len(lines) and len(lines[j + 1]) >= 4 and re.match(r"^[A-Z]", lines[j + 1]):
                    number, title = m.group(1), lines[j + 1]

            if number is None or number in seen_numbers:
                continue
            seen_numbers.add(number)
            found.append({"page": i, "number": number, "title": title})

    # keep only entries in increasing (unit, lesson) order *and* increasing
    # page order — filters out stray decimal-looking numbers picked up out
    # of reading order (e.g. from multi-column layouts)
    ordered = []
    last_num = (0, 0)
    last_page = -1
    for entry in found:
        u, l = (int(x) for x in entry["number"].split("."))
        if (u, l) > last_num and entry["page"] >= last_page:
            ordered.append(entry)
            last_num = (u, l)
            last_page = entry["page"]
    return ordered


def split_pdf_by_lesson(input_path: str, output_dir: str):
    """
    Splits input_path into one PDF per numbered lesson (e.g. 1.1, 1.2, 2.1 ...)
    found anywhere in the document, regardless of which Unit they're in.

    Returns a list of dicts: [{"title": ..., "filename": ..., "pages": "12-13"}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)
    reader = PdfReader(input_path)
    total_pages = len(reader.pages)

    lessons = _find_lesson_start_pages(reader)
    if not lessons:
        raise ValueError(
            "No numbered lessons (like \"1.1\", \"1.2\") were found in this PDF."
        )

    boundaries = [l["page"] for l in lessons] + [total_pages]
    results = []

    if lessons[0]["page"] > 0:
        writer = PdfWriter()
        for p in range(0, lessons[0]["page"]):
            writer.add_page(reader.pages[p])
        fname = "00_Front_Matter.pdf"
        with open(os.path.join(output_dir, fname), "wb") as f:
            writer.write(f)
        results.append({
            "title": "Front Matter",
            "filename": fname,
            "pages": f"1-{lessons[0]['page']}",
        })

    for idx, lesson in enumerate(lessons):
        start = lesson["page"]
        end = max(boundaries[idx + 1], start + 1)  # every lesson gets >= 1 page
        writer = PdfWriter()
        for p in range(start, end):
            writer.add_page(reader.pages[p])

        number = lesson["number"]
        title = f"{number} {lesson['title']}"
        safe_title = _clean_filename(lesson["title"], f"Lesson_{number}")
        fname = f"{number}_{safe_title}.pdf"

        with open(os.path.join(output_dir, fname), "wb") as f:
            writer.write(f)

        results.append({
            "title": title,
            "filename": fname,
            "pages": f"{start + 1}-{end}",
        })

    return results


def split_pdf_combined(input_path: str, output_dir: str, keyword: str = "Unit"):
    """
    Splits input_path into a folder per Unit, with a separate PDF for each
    lesson inside that unit — e.g.:

        01_Introduction_to_Parasite_and_Parasitology/
            1.1_Parasite_and_Parasitology.pdf
            1.2_Types_of_Parasites.pdf
        02_Disease_Caused_by_External_Parasites/
            2.1_Introduction_and_Types.pdf
            2.2_Important_Diseases.pdf

    If a unit has no detected lessons inside it, that unit's folder just
    gets one file covering the whole unit.

    Returns a flat list of dicts (handy for a flat UI list), each with an
    extra "group" key set to the unit's display title (or None for the
    front-matter entry):
        [{"title": ..., "filename": "<folder>/<file>.pdf", "pages": "12-13", "group": "Unit 1 · ..."}]
    """
    os.makedirs(output_dir, exist_ok=True)
    reader = PdfReader(input_path)
    total_pages = len(reader.pages)

    unit_starts = _find_unit_start_pages(reader, keyword=keyword)
    if not unit_starts:
        raise ValueError(
            f'No "{keyword}" heading found in this PDF, so it can\'t be split that way.'
        )
    unit_titles = _find_toc_titles(reader, expected_count=len(unit_starts))
    lessons = _find_lesson_start_pages(reader)

    unit_boundaries = unit_starts + [total_pages]
    results = []

    # Front matter (before the first unit) stays at the top level
    if unit_starts[0] > 0:
        writer = PdfWriter()
        for p in range(0, unit_starts[0]):
            writer.add_page(reader.pages[p])
        fname = "00_Front_Matter.pdf"
        with open(os.path.join(output_dir, fname), "wb") as f:
            writer.write(f)
        results.append({
            "title": "Front Matter",
            "filename": fname,
            "pages": f"1-{unit_starts[0]}",
            "group": None,
        })

    for u_idx, u_start in enumerate(unit_starts):
        u_end = unit_boundaries[u_idx + 1]  # exclusive
        u_num = u_idx + 1
        u_title = unit_titles[u_idx] if u_idx < len(unit_titles) else f"{keyword}_{u_num}"
        safe_unit_title = _clean_filename(u_title, f"{keyword}_{u_num}")
        unit_folder_name = f"{u_num:02d}_{safe_unit_title}"
        unit_folder_path = os.path.join(output_dir, unit_folder_name)
        os.makedirs(unit_folder_path, exist_ok=True)
        group_label = f"{keyword} {u_num} · {u_title}"

        # lessons that fall inside this unit's page range
        unit_lessons = [l for l in lessons if u_start <= l["page"] < u_end]

        if not unit_lessons:
            # no detected lessons in this unit — just save the whole unit as one file
            writer = PdfWriter()
            for p in range(u_start, u_end):
                writer.add_page(reader.pages[p])
            fname = f"{safe_unit_title}.pdf"
            with open(os.path.join(unit_folder_path, fname), "wb") as f:
                writer.write(f)
            results.append({
                "title": u_title,
                "filename": f"{unit_folder_name}/{fname}",
                "pages": f"{u_start + 1}-{u_end}",
                "group": group_label,
            })
            continue

        lesson_boundaries = [l["page"] for l in unit_lessons] + [u_end]
        for l_idx, lesson in enumerate(unit_lessons):
            # the first lesson in the unit absorbs any pages between the
            # unit heading and the first detected lesson heading
            l_start = u_start if l_idx == 0 else lesson["page"]
            l_end = max(lesson_boundaries[l_idx + 1], l_start + 1)

            writer = PdfWriter()
            for p in range(l_start, l_end):
                writer.add_page(reader.pages[p])

            number = lesson["number"]
            title = f"{number} {lesson['title']}"
            safe_title = _clean_filename(lesson["title"], f"Lesson_{number}")
            fname = f"{number}_{safe_title}.pdf"

            with open(os.path.join(unit_folder_path, fname), "wb") as f:
                writer.write(f)

            results.append({
                "title": title,
                "filename": f"{unit_folder_name}/{fname}",
                "pages": f"{l_start + 1}-{l_end}",
                "group": group_label,
            })

    return results


if __name__ == "__main__":
    # quick manual test
    import sys
    inp = sys.argv[1] if len(sys.argv) > 1 else "unit1.pdf"
    out = sys.argv[2] if len(sys.argv) > 2 else "output"
    mode = sys.argv[3] if len(sys.argv) > 3 else "unit"
    fn = {"unit": split_pdf_by_unit, "lesson": split_pdf_by_lesson, "combined": split_pdf_combined}[mode]
    for r in fn(inp, out):
        print(r)
