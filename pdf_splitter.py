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


def split_pdf_unit_and_lesson(input_path: str, output_dir: str, keyword: str = "Unit"):
    """
    Same folder-per-Unit layout as split_pdf_combined, but every unit
    folder ALWAYS gets the whole-unit PDF too, alongside its lesson-wise
    breakdown — so you get both granularities together, e.g.:

        01_Introduction_to_Parasite_and_Parasitology/
            00_Full_Unit.pdf                        <- the entire unit
            1.1_Parasite_and_Parasitology.pdf        <- just that lesson
            1.2_Types_of_Parasites.pdf

    (split_pdf_combined only gives the whole-unit file as a *fallback*
    when no lessons were detected; this one gives it every time.)

    Returns a flat list of dicts, same shape as split_pdf_combined.
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
        u_end = unit_boundaries[u_idx + 1]
        u_num = u_idx + 1
        u_title = unit_titles[u_idx] if u_idx < len(unit_titles) else f"{keyword}_{u_num}"
        safe_unit_title = _clean_filename(u_title, f"{keyword}_{u_num}")
        unit_folder_name = f"{u_num:02d}_{safe_unit_title}"
        unit_folder_path = os.path.join(output_dir, unit_folder_name)
        os.makedirs(unit_folder_path, exist_ok=True)
        group_label = f"{keyword} {u_num} \u00b7 {u_title}"

        # the whole unit, every time
        writer = PdfWriter()
        for p in range(u_start, u_end):
            writer.add_page(reader.pages[p])
        full_fname = "00_Full_Unit.pdf"
        with open(os.path.join(unit_folder_path, full_fname), "wb") as f:
            writer.write(f)
        results.append({
            "title": f"{u_title} (whole unit)",
            "filename": f"{unit_folder_name}/{full_fname}",
            "pages": f"{u_start + 1}-{u_end}",
            "group": group_label,
        })

        unit_lessons = [l for l in lessons if u_start <= l["page"] < u_end]
        if not unit_lessons:
            continue  # no lessons detected — the whole-unit file above is all there is

        lesson_boundaries = [l["page"] for l in unit_lessons] + [u_end]
        for l_idx, lesson in enumerate(unit_lessons):
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


def extract_text_hierarchy(input_path: str, output_dir: str, keyword: str = "Unit"):
    """
    Same Unit -> Lesson detection as split_pdf_combined, but instead of
    slicing PDF pages, this pulls out the actual TEXT content of each
    lesson's pages and saves it as a .txt file — e.g.:

        01_Introduction_to_Parasite_and_Parasitology/
            1.1_Parasite_and_Parasitology.txt   <- real extracted text, not a PDF
            1.2_Types_of_Parasites.txt

    Returns a flat list of dicts, same shape as split_pdf_combined.
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

    def _text_for_range(start, end):
        parts = []
        for p in range(start, end):
            parts.append(reader.pages[p].extract_text() or "")
        return "\n\n".join(parts).strip()

    unit_boundaries = unit_starts + [total_pages]
    results = []

    if unit_starts[0] > 0:
        fname = "00_Front_Matter.txt"
        with open(os.path.join(output_dir, fname), "w", encoding="utf-8") as f:
            f.write(_text_for_range(0, unit_starts[0]))
        results.append({
            "title": "Front Matter",
            "filename": fname,
            "pages": f"1-{unit_starts[0]}",
            "group": None,
        })

    for u_idx, u_start in enumerate(unit_starts):
        u_end = unit_boundaries[u_idx + 1]
        u_num = u_idx + 1
        u_title = unit_titles[u_idx] if u_idx < len(unit_titles) else f"{keyword}_{u_num}"
        safe_unit_title = _clean_filename(u_title, f"{keyword}_{u_num}")
        unit_folder_name = f"{u_num:02d}_{safe_unit_title}"
        unit_folder_path = os.path.join(output_dir, unit_folder_name)
        os.makedirs(unit_folder_path, exist_ok=True)
        group_label = f"{keyword} {u_num} \u00b7 {u_title}"

        unit_lessons = [l for l in lessons if u_start <= l["page"] < u_end]

        if not unit_lessons:
            fname = f"{safe_unit_title}.txt"
            with open(os.path.join(unit_folder_path, fname), "w", encoding="utf-8") as f:
                f.write(_text_for_range(u_start, u_end))
            results.append({
                "title": u_title,
                "filename": f"{unit_folder_name}/{fname}",
                "pages": f"{u_start + 1}-{u_end}",
                "group": group_label,
            })
            continue

        lesson_boundaries = [l["page"] for l in unit_lessons] + [u_end]
        for l_idx, lesson in enumerate(unit_lessons):
            l_start = u_start if l_idx == 0 else lesson["page"]
            l_end = max(lesson_boundaries[l_idx + 1], l_start + 1)

            number = lesson["number"]
            title = f"{number} {lesson['title']}"
            safe_title = _clean_filename(lesson["title"], f"Lesson_{number}")
            fname = f"{number}_{safe_title}.txt"

            with open(os.path.join(unit_folder_path, fname), "w", encoding="utf-8") as f:
                f.write(_text_for_range(l_start, l_end))

            results.append({
                "title": title,
                "filename": f"{unit_folder_name}/{fname}",
                "pages": f"{l_start + 1}-{l_end}",
                "group": group_label,
            })

    return results


def split_pdf_combined_with_text(input_path: str, output_dir: str, keyword: str = "Unit"):
    """
    Same Unit -> Lesson detection and folder hierarchy as split_pdf_combined,
    but writes BOTH a .pdf (the actual pages) AND a .txt (the extracted
    text) for every unit/lesson, side by side in the same folder — e.g.:

        01_Introduction_to_Parasite_and_Parasitology/
            1.1_Parasite_and_Parasitology.pdf
            1.1_Parasite_and_Parasitology.txt
            1.2_Types_of_Parasites.pdf
            1.2_Types_of_Parasites.txt

    Returns a flat list of dicts, same shape as split_pdf_combined (each
    unit/lesson contributes two entries: one for the .pdf, one for the .txt).
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

    def _write_pdf(start, end, path):
        writer = PdfWriter()
        for p in range(start, end):
            writer.add_page(reader.pages[p])
        with open(path, "wb") as f:
            writer.write(f)

    def _text_for_range(start, end):
        parts = [reader.pages[p].extract_text() or "" for p in range(start, end)]
        return "\n\n".join(parts).strip()

    unit_boundaries = unit_starts + [total_pages]
    results = []

    if unit_starts[0] > 0:
        start, end = 0, unit_starts[0]
        _write_pdf(start, end, os.path.join(output_dir, "00_Front_Matter.pdf"))
        with open(os.path.join(output_dir, "00_Front_Matter.txt"), "w", encoding="utf-8") as f:
            f.write(_text_for_range(start, end))
        for ext in ("pdf", "txt"):
            results.append({
                "title": "Front Matter",
                "filename": f"00_Front_Matter.{ext}",
                "pages": f"1-{end}",
                "group": None,
            })

    for u_idx, u_start in enumerate(unit_starts):
        u_end = unit_boundaries[u_idx + 1]
        u_num = u_idx + 1
        u_title = unit_titles[u_idx] if u_idx < len(unit_titles) else f"{keyword}_{u_num}"
        safe_unit_title = _clean_filename(u_title, f"{keyword}_{u_num}")
        unit_folder_name = f"{u_num:02d}_{safe_unit_title}"
        unit_folder_path = os.path.join(output_dir, unit_folder_name)
        os.makedirs(unit_folder_path, exist_ok=True)
        group_label = f"{keyword} {u_num} \u00b7 {u_title}"

        unit_lessons = [l for l in lessons if u_start <= l["page"] < u_end]

        if not unit_lessons:
            _write_pdf(u_start, u_end, os.path.join(unit_folder_path, f"{safe_unit_title}.pdf"))
            with open(os.path.join(unit_folder_path, f"{safe_unit_title}.txt"), "w", encoding="utf-8") as f:
                f.write(_text_for_range(u_start, u_end))
            for ext in ("pdf", "txt"):
                results.append({
                    "title": u_title,
                    "filename": f"{unit_folder_name}/{safe_unit_title}.{ext}",
                    "pages": f"{u_start + 1}-{u_end}",
                    "group": group_label,
                })
            continue

        lesson_boundaries = [l["page"] for l in unit_lessons] + [u_end]
        for l_idx, lesson in enumerate(unit_lessons):
            l_start = u_start if l_idx == 0 else lesson["page"]
            l_end = max(lesson_boundaries[l_idx + 1], l_start + 1)

            number = lesson["number"]
            title = f"{number} {lesson['title']}"
            safe_title = _clean_filename(lesson["title"], f"Lesson_{number}")
            base_name = f"{number}_{safe_title}"

            _write_pdf(l_start, l_end, os.path.join(unit_folder_path, f"{base_name}.pdf"))
            with open(os.path.join(unit_folder_path, f"{base_name}.txt"), "w", encoding="utf-8") as f:
                f.write(_text_for_range(l_start, l_end))

            for ext in ("pdf", "txt"):
                results.append({
                    "title": title,
                    "filename": f"{unit_folder_name}/{base_name}.{ext}",
                    "pages": f"{l_start + 1}-{l_end}",
                    "group": group_label,
                })

    return results


def _parse_page_ranges(ranges_str: str, total_pages: int):
    """
    Parse a string like "1-5, 6-10, 15-end" (1-indexed, inclusive) into a
    list of (start_0idx, end_0idx_exclusive) tuples.
    """
    parts = [p.strip() for p in ranges_str.split(",") if p.strip()]
    if not parts:
        raise ValueError("No page ranges given (e.g. \"1-5, 6-10\").")

    parsed = []
    for part in parts:
        m = re.match(r"^(\d+)\s*-\s*(\d+|end)$", part, re.IGNORECASE)
        if m:
            start = int(m.group(1))
            end = total_pages if m.group(2).lower() == "end" else int(m.group(2))
        elif re.match(r"^\d+$", part):
            start = end = int(part)
        else:
            raise ValueError(f'Could not understand the range "{part}". Use something like "1-5, 6-10".')

        if start < 1 or end > total_pages or start > end:
            raise ValueError(f'Range "{part}" is out of bounds for a {total_pages}-page PDF.')
        parsed.append((start - 1, end))  # 0-indexed, end exclusive
    return parsed


def split_pdf_by_range(input_path: str, output_dir: str, ranges_str: str):
    """
    Splits input_path into one PDF per page-range the caller specifies,
    e.g. ranges_str="1-5, 6-10, 11-end" — works on ANY PDF, scanned or not,
    since it doesn't try to read the content at all.

    Returns a list of dicts: [{"title": ..., "filename": ..., "pages": "1-5"}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)
    reader = PdfReader(input_path)
    total_pages = len(reader.pages)

    ranges = _parse_page_ranges(ranges_str, total_pages)
    results = []
    for idx, (start, end) in enumerate(ranges):
        writer = PdfWriter()
        for p in range(start, end):
            writer.add_page(reader.pages[p])
        fname = f"Pages_{start + 1}-{end}.pdf"
        with open(os.path.join(output_dir, fname), "wb") as f:
            writer.write(f)
        results.append({
            "title": f"Pages {start + 1}\u2013{end}",
            "filename": fname,
            "pages": f"{start + 1}-{end}",
        })
    return results


def split_pdf_fixed(input_path: str, output_dir: str, pages_per_chunk: int):
    """
    Splits input_path into equal-sized chunks of `pages_per_chunk` pages
    each — works on ANY PDF (scanned, text, mixed) since it never reads
    the content, just counts pages. Good fallback when a book has no
    machine-readable structure (e.g. a scanned nursery workbook).

    Returns a list of dicts: [{"title": ..., "filename": ..., "pages": "1-5"}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)
    reader = PdfReader(input_path)
    total_pages = len(reader.pages)

    if pages_per_chunk < 1:
        raise ValueError("Pages per file must be at least 1.")

    results = []
    part = 1
    for start in range(0, total_pages, pages_per_chunk):
        end = min(start + pages_per_chunk, total_pages)
        writer = PdfWriter()
        for p in range(start, end):
            writer.add_page(reader.pages[p])
        fname = f"Part_{part:02d}_Pages_{start + 1}-{end}.pdf"
        with open(os.path.join(output_dir, fname), "wb") as f:
            writer.write(f)
        results.append({
            "title": f"Part {part} (pages {start + 1}\u2013{end})",
            "filename": fname,
            "pages": f"{start + 1}-{end}",
        })
        part += 1
    return results


def split_pdf_per_page(input_path: str, output_dir: str):
    """
    Splits input_path into one PDF per single page. Works on any PDF.

    Returns a list of dicts: [{"title": ..., "filename": ..., "pages": "1-1"}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)
    reader = PdfReader(input_path)
    total_pages = len(reader.pages)

    results = []
    for i in range(total_pages):
        writer = PdfWriter()
        writer.add_page(reader.pages[i])
        fname = f"Page_{i + 1:03d}.pdf"
        with open(os.path.join(output_dir, fname), "wb") as f:
            writer.write(f)
        results.append({
            "title": f"Page {i + 1}",
            "filename": fname,
            "pages": f"{i + 1}-{i + 1}",
        })
    return results


def extract_selected_pages(input_path: str, output_dir: str, pages, combine: bool = False):
    """
    Extracts specific pages (1-indexed, order matters) chosen visually by
    the user — e.g. from a thumbnail-grid picker.

    combine=False -> one PDF per selected page (in the order given)
    combine=True  -> a single PDF containing just the selected pages, in
                     that order

    Returns a list of dicts like the other split_* functions.
    """
    os.makedirs(output_dir, exist_ok=True)
    reader = PdfReader(input_path)
    total_pages = len(reader.pages)

    if not pages:
        raise ValueError("No pages were selected.")

    for p in pages:
        if p < 1 or p > total_pages:
            raise ValueError(f"Page {p} is out of range for a {total_pages}-page PDF.")

    if combine:
        writer = PdfWriter()
        for p in pages:
            writer.add_page(reader.pages[p - 1])
        fname = "Selected_Pages.pdf"
        with open(os.path.join(output_dir, fname), "wb") as f:
            writer.write(f)
        return [{
            "title": f"Selected pages ({len(pages)} page{'s' if len(pages) != 1 else ''})",
            "filename": fname,
            "pages": ",".join(str(p) for p in pages),
        }]

    results = []
    for p in pages:
        writer = PdfWriter()
        writer.add_page(reader.pages[p - 1])
        fname = f"Page_{p:03d}.pdf"
        with open(os.path.join(output_dir, fname), "wb") as f:
            writer.write(f)
        results.append({
            "title": f"Page {p}",
            "filename": fname,
            "pages": f"{p}-{p}",
        })
    return results


def merge_pdfs(input_paths, output_dir: str, output_name: str = "Merged.pdf"):
    """
    Merges multiple PDFs (in the order given) into a single PDF.

    Returns a list with one dict: [{"title": ..., "filename": ..., "pages": "1-N"}]
    """
    os.makedirs(output_dir, exist_ok=True)
    if not input_paths:
        raise ValueError("No PDFs were provided to merge.")
    if len(input_paths) < 2:
        raise ValueError("Add at least 2 PDFs to merge.")

    writer = PdfWriter()
    total_pages = 0
    for path in input_paths:
        reader = PdfReader(path)
        for page in reader.pages:
            writer.add_page(page)
        total_pages += len(reader.pages)

    fname = output_name if output_name.lower().endswith(".pdf") else f"{output_name}.pdf"
    out_path = os.path.join(output_dir, fname)
    with open(out_path, "wb") as f:
        writer.write(f)

    return [{
        "title": f"Merged PDF ({len(input_paths)} files, {total_pages} pages)",
        "filename": fname,
        "pages": f"1-{total_pages}",
    }]


if __name__ == "__main__":
    # quick manual test
    import sys
    inp = sys.argv[1] if len(sys.argv) > 1 else "unit1.pdf"
    out = sys.argv[2] if len(sys.argv) > 2 else "output"
    mode = sys.argv[3] if len(sys.argv) > 3 else "unit"
    fn = {"unit": split_pdf_by_unit, "lesson": split_pdf_by_lesson, "combined": split_pdf_combined}[mode]
    for r in fn(inp, out):
        print(r)
