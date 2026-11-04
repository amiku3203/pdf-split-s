"""
pdf_converter.py
-----------------
Conversions between PDF, Word (.docx), and images (.jpg/.png).

- pdf_to_images: renders each page as an image (uses PyMuPDF — no external
  binary needed, works even on scanned PDFs since it just rasterizes pages).
- images_to_pdf: combines one or more images into a single PDF (uses Pillow).
- pdf_to_word: converts a PDF into an editable .docx, preserving layout
  reasonably well (uses pdf2docx).
- word_to_pdf: converts a .docx into a PDF (shells out to LibreOffice in
  headless mode — this is the one conversion that needs LibreOffice
  installed on the machine/server; see the note in README/app.py).
"""

import gc
import os
import subprocess
import pymupdf
from PIL import Image
from pdf2docx import Converter


def get_pdf_page_count(input_path: str) -> int:
    """
    Returns the total page count of a PDF quickly without loading all pages into memory.
    """
    doc = pymupdf.open(input_path)
    try:
        return doc.page_count
    finally:
        doc.close()
        del doc
        gc.collect()


def generate_single_thumbnail(input_path: str, thumbs_dir: str, page_num: int, width: int = 340) -> str:
    """
    Renders a small preview image (JPEG) of a single page (1-indexed) on demand.
    Cleans up memory immediately to prevent RAM bloat on large PDFs.
    """
    os.makedirs(thumbs_dir, exist_ok=True)
    fname = f"thumb_{page_num:04d}.jpg"
    out_path = os.path.join(thumbs_dir, fname)
    if os.path.exists(out_path):
        return out_path

    doc = pymupdf.open(input_path)
    try:
        if 1 <= page_num <= doc.page_count:
            page = doc[page_num - 1]
            rect = page.rect
            zoom = width / rect.width if rect.width else 1
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            try:
                pix.save(out_path)
            finally:
                del pix
                del page
        else:
            raise ValueError(f"Page {page_num} is out of bounds (1..{doc.page_count}).")
    finally:
        doc.close()
        del doc
        gc.collect()
    return out_path


def generate_thumbnails(input_path: str, thumbs_dir: str, width: int = 340, max_pages: int = None):
    """
    Renders preview images for the visual page picker.
    If max_pages is given, only pre-renders up to that many pages upfront,
    and returns the total page count. Remaining pages are loaded lazily on demand.
    """
    os.makedirs(thumbs_dir, exist_ok=True)
    doc = pymupdf.open(input_path)
    try:
        page_count = doc.page_count
        render_limit = min(page_count, max_pages) if (max_pages and max_pages > 0) else page_count
        for i in range(render_limit):
            page = doc[i]
            rect = page.rect
            zoom = width / rect.width if rect.width else 1
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            try:
                pix.save(os.path.join(thumbs_dir, f"thumb_{i + 1:04d}.jpg"))
            finally:
                del pix
                del page
            if (i + 1) % 20 == 0:
                gc.collect()
    finally:
        doc.close()
        del doc
        gc.collect()
    return page_count


def pdf_to_images(input_path: str, output_dir: str, fmt: str = "jpg", dpi: int = 150):
    """
    Renders every page of input_path as an image file in output_dir.
    Cleans up memory page-by-page.

    Returns a list of dicts: [{"title": "Page 1", "filename": "Page_001.jpg", "pages": "1-1"}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)
    fmt = fmt.lower().lstrip(".")
    if fmt not in ("jpg", "jpeg", "png"):
        raise ValueError('Image format must be "jpg" or "png".')

    doc = pymupdf.open(input_path)
    results = []
    try:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            fname = f"Page_{i + 1:03d}.{fmt}"
            try:
                pix.save(os.path.join(output_dir, fname))
            finally:
                del pix
                del page
            results.append({
                "title": f"Page {i + 1}",
                "filename": fname,
                "pages": f"{i + 1}-{i + 1}",
            })
            if (i + 1) % 20 == 0:
                gc.collect()
    finally:
        doc.close()
        del doc
        gc.collect()

    if not results:
        raise ValueError("This PDF has no pages to convert.")
    return results


def images_to_pdf(image_paths, output_dir: str, output_name: str = "Combined.pdf"):
    """
    Combines one or more image files (JPG/PNG/etc.) into a single PDF,
    in the order given. Uses PyMuPDF when possible to stream without loading
    all uncompressed bitmaps into memory at once.
    """
    os.makedirs(output_dir, exist_ok=True)
    if not image_paths:
        raise ValueError("No images were provided to combine.")

    fname = output_name if output_name.lower().endswith(".pdf") else f"{output_name}.pdf"
    out_path = os.path.join(output_dir, fname)

    # Use PyMuPDF to convert each image to PDF page and merge
    out = pymupdf.open()
    try:
        for p in image_paths:
            img_doc = pymupdf.open(p)
            try:
                pdf_bytes = img_doc.convert_to_pdf()
            finally:
                img_doc.close()
                del img_doc

            single_pdf = pymupdf.open("pdf", pdf_bytes)
            try:
                out.insert_pdf(single_pdf)
            finally:
                single_pdf.close()
                del single_pdf
                del pdf_bytes

        out.save(out_path, garbage=2, deflate=True)
    finally:
        out.close()
        del out
        gc.collect()

    return [{
        "title": f"Combined PDF ({len(image_paths)} page{'s' if len(image_paths) != 1 else ''})",
        "filename": fname,
        "pages": f"1-{len(image_paths)}",
    }]


def pdf_to_word(input_path: str, output_dir: str, output_name: str = None):
    """
    Converts input_path into an editable .docx file in output_dir.

    Returns a list with one dict: [{"title": ..., "filename": ..., "pages": "1-N"}]
    """
    os.makedirs(output_dir, exist_ok=True)
    reader_pages = pymupdf.open(input_path).page_count

    base = output_name or os.path.splitext(os.path.basename(input_path))[0]
    base = base.rsplit(".", 1)[0] if base.lower().endswith(".pdf") else base
    fname = f"{base}.docx"
    out_path = os.path.join(output_dir, fname)

    cv = Converter(input_path)
    try:
        cv.convert(out_path)
    finally:
        cv.close()

    return [{
        "title": f"{base}.docx",
        "filename": fname,
        "pages": f"1-{reader_pages}",
    }]


def word_to_pdf(input_path: str, output_dir: str, output_name: str = None):
    """
    Converts a .docx (or .doc) file into a PDF using LibreOffice headless
    mode. Requires `soffice` (LibreOffice) to be installed on the machine
    running this — it is NOT a pip package.

    Returns a list with one dict: [{"title": ..., "filename": ..., "pages": "?"}]
    """
    os.makedirs(output_dir, exist_ok=True)

    # LibreOffice names its output after the input file, so if the caller
    # wants a nicer name (e.g. the user's original upload name), stage a
    # renamed copy of the input first.
    if output_name:
        base = os.path.splitext(output_name)[0]
        ext = os.path.splitext(input_path)[1] or ".docx"
        staged_path = os.path.join(os.path.dirname(input_path), f"{base}{ext}")
        if staged_path != input_path:
            import shutil
            shutil.copyfile(input_path, staged_path)
            input_path = staged_path

    try:
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", output_dir, input_path],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise ValueError(
            "Word\u2192PDF needs LibreOffice installed on the server (the `soffice` command). "
            "It isn't available here — install LibreOffice or run this conversion on a machine that has it."
        )
    except subprocess.CalledProcessError as e:
        raise ValueError(f"Conversion failed: {e.stderr.decode(errors='ignore')[:300]}")
    except subprocess.TimeoutExpired:
        raise ValueError("Conversion took too long and was stopped.")

    base = os.path.splitext(os.path.basename(input_path))[0]
    fname = f"{base}.pdf"
    out_path = os.path.join(output_dir, fname)
    if not os.path.exists(out_path):
        raise ValueError("Conversion did not produce a PDF file.")

    try:
        pages = pymupdf.open(out_path).page_count
    except Exception:
        pages = "?"

    return [{
        "title": fname,
        "filename": fname,
        "pages": f"1-{pages}",
    }]
