"""
fastapi_app.py
--------------
Unified FastAPI backend for the PDF Toolkit:
- PDF Split: Unit, Lesson, Combined, Range, Fixed, Per-page, Nepali text & spellcheck, Visual page picker, Manual sections
- PDF Merge: Multi-PDF upload with drag-and-drop custom ordering
- PDF Convert: PDF to Word, PDF to Image, Word to PDF, Image to PDF
- PDF Compress: Lossless and visually-lossless stream & font deflation, keeping text/vector paths razor-sharp (<100MB guaranteed)
- Streaming Chunked Upload: 10MB chunk pipeline with automatic server-side compression upon assembly before saving
- Direct Downloads and Full-Job Zip Downloads
"""

import gc
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Optional, Literal
from urllib.parse import quote

import pymupdf
import requests
from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from pdf_splitter import (
    split_pdf_by_unit,
    split_pdf_by_lesson,
    split_pdf_combined,
    split_pdf_unit_and_lesson,
    extract_text_hierarchy,
    split_pdf_combined_with_text,
    split_pdf_by_range,
    split_pdf_fixed,
    split_pdf_per_page,
    extract_selected_pages,
    merge_pdfs,
)
from nepali_text import extract_nepali_text_hierarchy, add_custom_word, _safe_name
from pdf_converter import (
    pdf_to_images,
    images_to_pdf,
    pdf_to_word,
    word_to_pdf,
    generate_thumbnails,
    generate_single_thumbnail,
)
from pdf_compressor import compress_pdf

logger = logging.getLogger("fastapi_app")
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="PDF Toolkit & Compressor",
    description="Unified high-performance PDF Toolkit: Split, Merge, Convert, and Lossless Compression with Chunked Streaming.",
    version="2.0.0",
)

# Allowed origins for CORS (CRM and local development)
ALLOWED_ORIGINS = [
    "http://localhost",
    "http://127.0.0.1",
    "https://crm.smatoroai.com",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://localhost:5000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:5000",
]

# Enable CORS for frontend and external clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Jinja2 templates configuration with custom url_for supporting both Flask and FastAPI syntax
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _custom_url_for(name: str, **params) -> str:
    if name == "static":
        filename = params.get("filename") or params.get("path", "")
        return f"/static/{filename}"
    return f"/{name}"


templates.env.globals["url_for"] = _custom_url_for


# ------------------------------------------------------------- HELPERS ---

def _valid_job_id(job_id: str) -> bool:
    return bool(job_id and isinstance(job_id, str) and re.fullmatch(r"[0-9a-zA-Z_\-]+", job_id))


def _new_job() -> tuple[str, Path]:
    job_id = uuid.uuid4().hex[:12]
    job_output_dir = OUTPUT_DIR / job_id
    job_output_dir.mkdir(parents=True, exist_ok=True)
    return job_id, job_output_dir


def _find_job_source(job_id: str) -> Path:
    # 1. Exact match in uploads ({job_id}_source.*)
    matches = [p for p in UPLOAD_DIR.glob(f"{job_id}_source.*") if p.is_file() and p.stat().st_size > 0]
    if matches:
        return matches[0]

    # 2. Any file in uploads starting with {job_id}
    matches = [p for p in UPLOAD_DIR.glob(f"{job_id}*.*") if p.is_file() and p.stat().st_size > 0]
    if matches:
        return matches[0]

    # 3. Search in output directory for compressed or assembled outputs
    job_out = OUTPUT_DIR / job_id
    if job_out.is_dir():
        out_matches = [p for p in job_out.glob("compressed_*.pdf") if p.is_file() and p.stat().st_size > 0]
        if out_matches:
            return out_matches[0]
        out_matches = [p for p in job_out.glob("*.pdf") if p.is_file() and p.stat().st_size > 0]
        if out_matches:
            return out_matches[0]

    raise HTTPException(
        status_code=404,
        detail=f"Uploaded file for job '{job_id}' not found. Please upload again."
    )


def _fetch_from_url(url: str, dest_path: Path):
    with requests.get(url, stream=True, timeout=(30, 600)) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


async def _parse_request_data(request: Request):
    """
    Parses request body whether JSON or multipart/form-data.
    Returns (dict_data, form_files, single_file).
    """
    ct = request.headers.get("content-type", "")
    data = {}
    files = []
    file = None

    if "application/json" in ct:
        try:
            data = await request.json()
        except Exception:
            data = {}
    elif "multipart/form-data" in ct or "application/x-www-form-urlencoded" in ct:
        form = await request.form()
        for k, v in form.items():
            if hasattr(v, "filename"):
                if k == "file":
                    file = v
            else:
                data[k] = v
        files = form.getlist("files")
        if not file and "file" in form:
            f = form["file"]
            if hasattr(f, "filename"):
                file = f

    for k, v in request.query_params.items():
        if k not in data:
            data[k] = v

    return data, files, file


async def _save_input_for_job(job_id: str, data: dict, file: Optional[UploadFile]) -> Path:
    existing = list(UPLOAD_DIR.glob(f"{job_id}_source.*"))
    if existing and existing[0].stat().st_size > 0:
        return existing[0]

    ref_id = data.get("job_id")
    if ref_id and _valid_job_id(ref_id):
        ref_matches = list(UPLOAD_DIR.glob(f"{ref_id}_source.*"))
        if ref_matches and ref_matches[0].stat().st_size > 0:
            return ref_matches[0]

    url = data.get("pdf_url")
    if url:
        try:
            ext = os.path.splitext(url.split("?")[0])[1] or ".pdf"
            path = UPLOAD_DIR / f"{job_id}_source{ext}"
            await run_in_threadpool(_fetch_from_url, url, path)
            return path
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not fetch file from URL: {e}")

    if file and file.filename:
        ext = os.path.splitext(file.filename)[1] or ".pdf"
        path = UPLOAD_DIR / f"{job_id}_source{ext}"
        try:
            with open(path, "wb") as dst:
                shutil.copyfileobj(file.file, dst, length=1024 * 1024)
            return path
        finally:
            await file.close()

    raise HTTPException(status_code=400, detail="Provide either a file upload, a 'pdf_url', or a valid 'job_id'.")


async def _fetch_one_input(data: dict, file: Optional[UploadFile], prefix: str = "input") -> tuple[Path, str]:
    job_tag = uuid.uuid4().hex[:8]
    url = data.get("pdf_url")
    if url:
        try:
            orig_name = os.path.basename(url.split("?")[0]) or "file.pdf"
            ext = os.path.splitext(orig_name)[1] or ".pdf"
            path = UPLOAD_DIR / f"{job_tag}_{prefix}{ext}"
            await run_in_threadpool(_fetch_from_url, url, path)
            return path, orig_name
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not fetch file from URL: {e}")

    if file and file.filename:
        orig_name = file.filename or "file.pdf"
        ext = os.path.splitext(orig_name)[1] or ".pdf"
        path = UPLOAD_DIR / f"{job_tag}_{prefix}{ext}"
        try:
            with open(path, "wb") as dst:
                shutil.copyfileobj(file.file, dst, length=1024 * 1024)
            return path, orig_name
        finally:
            await file.close()

    raise HTTPException(status_code=400, detail="Provide either a 'pdf_url' or an uploaded file.")


# ---------------------------------------------------- SECTIONS BOOKKEEPING ---

def _sections_file(job_id: str) -> Path:
    return OUTPUT_DIR / job_id / "sections.json"


def _load_sections(job_id: str) -> list:
    sf = _sections_file(job_id)
    try:
        with open(sf, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save_sections(job_id: str, sections: list):
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    with open(_sections_file(job_id), "w", encoding="utf-8") as f:
        json.dump(sections, f, ensure_ascii=False)


def _pages_label(pages: list[int]) -> str:
    if not pages:
        return ""
    runs, start, prev = [], pages[0], pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        runs.append((start, prev))
        start = prev = p
    runs.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def _sections_response(job_id: str) -> dict:
    sections = _load_sections(job_id)
    for sec in sections:
        sec["download_url"] = f"/download/{job_id}/{quote(sec['filename'])}"
    return {
        "job_id": job_id,
        "sections": sections,
        "download_all_url": f"/download-all/{job_id}",
    }


def _crop_page(page, frac):
    fx0, fy0, fx1, fy1 = frac
    w, h = page.rect.width, page.rect.height
    r = pymupdf.Rect(fx0 * w, fy0 * h, fx1 * w, fy1 * h) * page.derotation_matrix
    base = page.cropbox
    r = pymupdf.Rect(r.x0 + base.x0, r.y0 + base.y0, r.x1 + base.x0, r.y1 + base.y0)
    page.set_cropbox(r)


# ================================================================ ROUTES ===

@app.get("/")
async def index(request: Request):
    """Serves the unified frontend HTML with Split, Merge, Convert, and Compress tabs."""
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "js_version": uuid.uuid4().hex[:8]},
    )


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "pdf-toolkit-fastapi", "version": "2.0.0"}


# ---------------------------------------------------------- CHUNK UPLOAD ---

@app.post("/upload-chunk")
async def upload_chunk(
    chunk: UploadFile = File(...),
    job_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form("file.pdf"),
    compress_on_complete: bool = Form(True),
    mode: Literal["lossless", "visually_lossless"] = Form("visually_lossless"),
    image_quality: int = Form(90),
):
    """
    Receives 10MB chunks directly to disk to bypass 413 limits.
    When the final chunk is assembled, automatically compresses the PDF
    on the server side right after assembly before saving, without requiring
    any manual compression step from the user.
    """
    if not _valid_job_id(job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    safe_name = Path(filename).name
    ext = os.path.splitext(safe_name)[1] or ".pdf"
    target_path = UPLOAD_DIR / f"{job_id}_source{ext}"

    write_mode = "wb" if chunk_index == 0 else "ab"
    try:
        with open(target_path, write_mode) as dst:
            shutil.copyfileobj(chunk.file, dst, length=1024 * 1024)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save chunk {chunk_index}: {exc}")
    finally:
        await chunk.close()

    # Verify magic bytes on the first chunk of PDF files
    if chunk_index == 0 and ext.lower() == ".pdf":
        with open(target_path, "rb") as f:
            header = f.read(5)
            if header != b"%PDF-":
                try:
                    target_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise HTTPException(status_code=422, detail="Invalid PDF file (missing %PDF- header).")

    is_complete = (chunk_index + 1 >= total_chunks)
    compressed = False
    stats_data = {}
    download_url = None
    out_name = None

    if is_complete and compress_on_complete and ext.lower() == ".pdf" and target_path.is_file():
        clean_name = safe_name if not safe_name.startswith("compressed_") else safe_name
        out_name = f"compressed_{clean_name}"
        job_output_dir = OUTPUT_DIR / job_id
        job_output_dir.mkdir(parents=True, exist_ok=True)
        final_output_path = job_output_dir / out_name
        temp_comp_path = UPLOAD_DIR / f"{job_id}_compressed_tmp.pdf"

        try:
            stats = await run_in_threadpool(
                compress_pdf,
                input_path=str(target_path),
                output_path=str(temp_comp_path),
                mode=mode,
                image_quality=image_quality,
            )

            if temp_comp_path.is_file() and temp_comp_path.stat().st_size > 0:
                shutil.copyfile(temp_comp_path, final_output_path)
                try:
                    temp_comp_path.replace(target_path)
                except Exception:
                    shutil.copyfile(temp_comp_path, target_path)

                compressed = True
                stats_data = stats
                download_url = f"/download/{job_id}/{out_name}"

                # Cache stats for instant retrieval by /compress
                stats_cache_file = job_output_dir / "compress_stats.json"
                stats["job_id"] = job_id
                stats["filename"] = out_name
                stats["download_url"] = download_url
                with open(stats_cache_file, "w", encoding="utf-8") as sf:
                    json.dump(stats, sf)

        except Exception as exc:
            logger.warning(f"Automatic compression failed for job {job_id}: {exc}")
        finally:
            if temp_comp_path.is_file():
                try:
                    temp_comp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    return {
        "status": "ok",
        "job_id": job_id,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "complete": is_complete,
        "compressed": compressed,
        "original_size_mb": stats_data.get("original_size_mb"),
        "compressed_size_mb": stats_data.get("compressed_size_mb"),
        "saved_mb": stats_data.get("saved_mb"),
        "savings_percent": stats_data.get("savings_percent"),
        "filename": out_name,
        "download_url": download_url,
    }


# ------------------------------------------------------------- PREVIEW ---

@app.post("/preview")
async def preview(request: Request):
    """
    Loads a PDF and returns {job_id, page_count}.
    Pre-renders the first batch of 12 thumbnails for instant visual picker opening.
    """
    data, files, file = await _parse_request_data(request)
    job_id = data.get("job_id")
    if not job_id or not _valid_job_id(job_id):
        job_id, job_output_dir = _new_job()
    else:
        job_output_dir = OUTPUT_DIR / job_id
        job_output_dir.mkdir(parents=True, exist_ok=True)

    source_path = await _save_input_for_job(job_id, data, file)
    thumbs_dir = job_output_dir / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    try:
        page_count = await run_in_threadpool(
            generate_thumbnails,
            str(source_path),
            str(thumbs_dir),
            width=340,
            max_pages=12,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read that PDF: {exc}")

    return {"job_id": job_id, "page_count": page_count}


@app.get("/thumbnail/{job_id}/{page_num}")
async def thumbnail(job_id: str, page_num: int):
    if not _valid_job_id(job_id):
        raise HTTPException(status_code=404, detail="Unknown job.")

    thumbs_dir = OUTPUT_DIR / job_id / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    fname = f"thumb_{page_num:04d}.jpg"
    thumb_path = thumbs_dir / fname

    if not thumb_path.is_file():
        source_path = _find_job_source(job_id)
        try:
            await run_in_threadpool(
                generate_single_thumbnail,
                str(source_path),
                str(thumbs_dir),
                page_num,
                width=340,
            )
        except Exception as exc:
            raise HTTPException(status_code=404, detail=f"Could not generate thumbnail for page {page_num}: {exc}")

    if not thumb_path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found.")

    return FileResponse(str(thumb_path), media_type="image/jpeg")


@app.post("/split-pages")
async def split_pages(request: Request):
    """
    Extracts pages selected by the user in the thumbnail grid.
    body: {"job_id": "...", "pages": [1,2,5,6], "combine": true|false}
    """
    data, _, _ = await _parse_request_data(request)
    job_id = data.get("job_id")
    pages = data.get("pages") or []
    combine = bool(data.get("combine"))

    if not job_id:
        raise HTTPException(status_code=400, detail="Missing job_id — load the pages first.")

    source_path = _find_job_source(job_id)
    job_output_dir = OUTPUT_DIR / job_id
    try:
        pages_int = [int(p) for p in pages]
        results = await run_in_threadpool(
            extract_selected_pages,
            str(source_path),
            str(job_output_dir),
            pages_int,
            combine=combine,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to split pages: {exc}")

    for r in results:
        r["download_url"] = f"/download/{job_id}/{quote(r['filename'])}"

    return {
        "job_id": job_id,
        "chapter_count": len(results),
        "chapters": results,
        "download_all_url": f"/download-all/{job_id}",
    }


@app.get("/page-preview/{job_id}/{page}")
async def page_preview(
    job_id: str,
    page: int,
    x0: Optional[float] = None,
    y0: Optional[float] = None,
    x1: Optional[float] = None,
    y1: Optional[float] = None,
    wx0: Optional[float] = -1.0,
    wx1: Optional[float] = -1.0,
    mode: str = "band",
):
    """PNG preview of a page with highlighted line and word bounding boxes for Nepali spell check."""
    if not _valid_job_id(job_id):
        raise HTTPException(status_code=404, detail="Unknown job.")

    source = _find_job_source(job_id)

    def _render():
        doc = pymupdf.open(str(source))
        try:
            if not 1 <= page <= doc.page_count:
                return None
            pg = doc[page - 1]
            clip = None
            if x0 is not None and y0 is not None and x1 is not None and y1 is not None:
                line = pymupdf.Rect(x0 - 2, y0 - 1, x1 + 2, y1 + 1)
                pg.draw_rect(line, color=None, fill=(1, 0.86, 0.1), fill_opacity=0.30)
                if wx0 is not None and wx1 is not None and 0 <= wx0 < wx1 <= 1:
                    word = pymupdf.Rect(line.x0 + wx0 * line.width, line.y0, line.x0 + wx1 * line.width, line.y1)
                    pg.draw_rect(word, color=(0.85, 0.1, 0.1), width=1.3, fill=(1, 0.1, 0.1), fill_opacity=0.16)
                if mode != "full":
                    clip = pymupdf.Rect(0, max(0, y0 - 70), pg.rect.width, min(pg.rect.height, y1 + 70))
            pix = pg.get_pixmap(dpi=130, clip=clip)
            return pix.tobytes("png")
        finally:
            doc.close()

    png_bytes = await run_in_threadpool(_render)
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="Page not found.")

    return Response(content=png_bytes, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/dictionary/add")
async def dictionary_add(request: Request):
    data, _, _ = await _parse_request_data(request)
    word = data.get("word", "")
    if not add_custom_word(word):
        raise HTTPException(status_code=422, detail="That is not a Nepali word.")
    return {"ok": True}


# ---------------------------------------------------- SPELL CHECK ROUTER ---

spellcheck_router = APIRouter(prefix="/api/spellcheck", tags=["Spell Check"])


@spellcheck_router.post("/analyze")
async def analyze_pdf_spelling(request: Request):
    """
    Dedicated standalone spell-check analysis endpoint.
    Accepts JSON (job_id, pdf_url, ranges) or multipart/form-data (file, job_id, ranges).
    Extracts text, checks spelling against Devanagari lexicon,
    and returns mistake statistics, suggestions, on-page bounding boxes, and CSV report link.
    """
    data, files, file = await _parse_request_data(request)
    job_id = data.get("job_id")
    if job_id and _valid_job_id(job_id):
        job_output_dir = OUTPUT_DIR / job_id
        job_output_dir.mkdir(parents=True, exist_ok=True)
        job_input_path = _find_job_source(job_id)
    else:
        job_id, job_output_dir = _new_job()
        job_input_path, _ = await _fetch_one_input(data, file, "spellcheck")

    ranges_str = data.get("ranges", "")

    # Preserve source for visual /page-preview/{job_id}/{page}
    source_copy = UPLOAD_DIR / f"{job_id}_source.pdf"
    if str(job_input_path) != str(source_copy):
        shutil.copyfile(job_input_path, source_copy)

    findings_payload = {}
    notice = ""

    def _execute():
        nonlocal notice
        _, notc = extract_nepali_text_hierarchy(
            input_path=str(job_input_path),
            output_dir=str(job_output_dir),
            ranges_str=ranges_str,
            with_pdf=False,
            check_spelling=True,
            findings_out=findings_payload,
        )
        notice = notc

    try:
        await run_in_threadpool(_execute)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Spell-check failed: {exc}")
    finally:
        gc.collect()

    spellcheck_data = findings_payload.get("spellcheck")
    if not spellcheck_data:
        raise HTTPException(status_code=500, detail="Spell-check engine returned empty findings.")

    return {
        "status": "ok",
        "job_id": job_id,
        "notice": notice,
        "csv_report_url": f"/download/{job_id}/Spellcheck_Report.csv",
        "download_all_url": f"/download-all/{job_id}",
        "spellcheck": spellcheck_data,
    }


@spellcheck_router.post("/whitelist/add")
async def add_whitelist_word(request: Request):
    """
    Adds a custom Nepali word to the dictionary whitelist so it is not flagged again.
    Accepts JSON {"word": "..."} or Form data word="...".
    """
    data, _, _ = await _parse_request_data(request)
    word = (data.get("word") or "").strip()
    if not word:
        raise HTTPException(status_code=400, detail="Missing 'word' parameter.")
    if not add_custom_word(word):
        raise HTTPException(status_code=422, detail="That is not a Nepali word.")
    return {
        "status": "ok",
        "message": f"'{word}' added to custom dictionary.",
        "ok": True,
    }


app.include_router(spellcheck_router)


# ------------------------------------------------------ MANUAL SECTIONS ---

@app.get("/sections/{job_id}")
async def list_sections(job_id: str):
    if not _valid_job_id(job_id):
        raise HTTPException(status_code=404, detail="Unknown job.")
    return _sections_response(job_id)


@app.post("/create-section")
async def create_section(request: Request):
    data, _, _ = await _parse_request_data(request)
    job_id = data.get("job_id")
    if not _valid_job_id(job_id):
        raise HTTPException(status_code=400, detail="Missing or invalid job — load the pages first.")

    source_path = _find_job_source(job_id)

    def _do_create():
        src = pymupdf.open(str(source_path))
        out = None
        target_filepath = None
        try:
            total = src.page_count
            pages = sorted({int(p) for p in (data.get("pages") or [])})
            if not pages:
                raise ValueError("Pick at least one page for this section.")
            if pages[0] < 1 or pages[-1] > total:
                raise ValueError(f"Pages must be between 1 and {total}.")

            crops = {}
            for key, rect in (data.get("crops") or {}).items():
                p = int(key)
                if p not in pages:
                    continue
                x0, y0, x1, y1 = (min(max(float(v), 0.0), 1.0) for v in rect)
                if x1 - x0 < 0.03 or y1 - y0 < 0.03:
                    raise ValueError(f"The crop on page {p} is too small.")
                if not (x0 <= 0.005 and y0 <= 0.005 and x1 >= 0.995 and y1 >= 0.995):
                    crops[p] = (x0, y0, x1, y1)

            sections = _load_sections(job_id)
            seq = max((sec["id"] for sec in sections), default=0) + 1
            title = (data.get("name") or "").strip()[:120] or f"Section {seq}"
            filename = f"{seq:02d}_{_safe_name(title, f'Section_{seq}')}.pdf"

            out_dir = OUTPUT_DIR / job_id
            out_dir.mkdir(parents=True, exist_ok=True)
            target_filepath = out_dir / filename

            out = pymupdf.open()
            for p in pages:
                out.insert_pdf(src, from_page=p - 1, to_page=p - 1)
                if p in crops:
                    _crop_page(out[len(out) - 1], crops[p])
            out.save(str(target_filepath), garbage=2, deflate=True)

            sections.append({
                "id": seq,
                "title": title,
                "filename": filename,
                "pages": _pages_label(pages),
                "page_list": pages,
                "cropped": sorted(crops),
                "group": None,
            })
            _save_sections(job_id, sections)
            return _sections_response(job_id)
        finally:
            if out:
                out.close()
            src.close()
            gc.collect()

    try:
        return await run_in_threadpool(_do_create)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create section: {exc}")


@app.post("/delete-section")
async def delete_section(request: Request):
    data, _, _ = await _parse_request_data(request)
    job_id = data.get("job_id")
    if not _valid_job_id(job_id):
        raise HTTPException(status_code=400, detail="Missing or invalid job.")

    target_id = data.get("id")
    sections = _load_sections(job_id)
    keep = []
    for sec in sections:
        if sec["id"] == target_id:
            try:
                (OUTPUT_DIR / job_id / sec["filename"]).unlink(missing_ok=True)
            except OSError:
                pass
        else:
            keep.append(sec)
    _save_sections(job_id, keep)
    return _sections_response(job_id)


def _fallback_split(input_path: str, output_dir: str, pages_per_chunk: int = 5) -> tuple[list, str]:
    """
    Safely splits a PDF when heading/lesson detection fails, ensuring the user always receives
    valid split files and never encounters an unhandled 422 error.
    """
    try:
        doc = pymupdf.open(input_path)
        total_pages = doc.page_count
        doc.close()
    except Exception:
        total_pages = 1

    if total_pages <= 5:
        chaps = split_pdf_per_page(input_path, output_dir)
        note = f"Document has {total_pages} page(s). Automatically split into individual pages."
    else:
        chunk_size = max(1, pages_per_chunk)
        chaps = split_pdf_fixed(input_path, output_dir, chunk_size)
        note = f"Automatically split into {len(chaps)} parts ({chunk_size} pages each)."
    return chaps, note


# ---------------------------------------------------------------- SPLIT ---

@app.post("/split")
async def split(request: Request):
    """
    Splits PDF using specified mode:
    unit | lesson | combined | unit_and_lesson | extract_text | combined_with_text |
    nepali_text | nepali_pdf_text | nepali_spellcheck | range | fixed | per_page
    """
    data, _, file = await _parse_request_data(request)

    # Sanitize job_id from JSON, Form, or Query parameters
    raw_job_id = data.get("job_id") or data.get("jobId") or data.get("job") or data.get("id")
    job_id = str(raw_job_id).strip().strip('"\' ') if raw_job_id else None
    if job_id and job_id.lower() in ("undefined", "null", "none", ""):
        job_id = None

    if job_id and _valid_job_id(job_id):
        job_output_dir = OUTPUT_DIR / job_id
        job_output_dir.mkdir(parents=True, exist_ok=True)
        job_input_path = _find_job_source(job_id)
    else:
        job_id, job_output_dir = _new_job()
        job_input_path, _ = await _fetch_one_input(data, file, "split")

    mode = str(data.get("mode") or "unit").strip()
    keyword = str(data.get("keyword") or "Unit").strip()
    ranges_str = str(data.get("ranges") or "").strip()
    try:
        pages_per_chunk = max(1, int(data.get("pages_per_chunk", 5)))
    except (TypeError, ValueError):
        pages_per_chunk = 5

    notice = ""
    spellcheck_findings = {}

    if mode == "nepali_spellcheck":
        shutil.copyfile(job_input_path, UPLOAD_DIR / f"{job_id}_source.pdf")

    def _do_split():
        nonlocal notice, spellcheck_findings
        in_str = str(job_input_path)
        out_str = str(job_output_dir)

        try:
            if mode == "lesson":
                return split_pdf_by_lesson(in_str, out_str)
            elif mode == "combined":
                return split_pdf_combined(in_str, out_str, keyword=keyword)
            elif mode == "unit_and_lesson":
                return split_pdf_unit_and_lesson(in_str, out_str, keyword=keyword)
            elif mode == "extract_text":
                return extract_text_hierarchy(in_str, out_str, keyword=keyword)
            elif mode == "combined_with_text":
                return split_pdf_combined_with_text(in_str, out_str, keyword=keyword)
            elif mode in ("nepali_text", "nepali_pdf_text", "nepali_spellcheck"):
                chaps, notc = extract_nepali_text_hierarchy(
                    in_str, out_str,
                    ranges_str=ranges_str, with_pdf=(mode == "nepali_pdf_text"),
                    check_spelling=(mode == "nepali_spellcheck"),
                    findings_out=spellcheck_findings,
                )
                notice = notc
                return chaps
            elif mode == "range":
                return split_pdf_by_range(in_str, out_str, ranges_str)
            elif mode == "fixed":
                return split_pdf_fixed(in_str, out_str, pages_per_chunk)
            elif mode == "per_page":
                return split_pdf_per_page(in_str, out_str)
            else:
                return split_pdf_by_unit(in_str, out_str, keyword=keyword)
        except ValueError as exc:
            # Graceful fallback: when heading/lesson detection fails on a PDF,
            # avoid throwing a 422 error and safely deliver split content to the user.
            logger.info(f"Split mode '{mode}' failed with ValueError: {exc}. Performing safe fallback split.")
            fallback_chaps, fallback_note = _fallback_split(in_str, out_str, pages_per_chunk)
            notice = f"Notice: {exc} — {fallback_note}"
            return fallback_chaps

    try:
        chapters = await run_in_threadpool(_do_split)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Splitting failed: {exc}")
    finally:
        gc.collect()

    for ch in chapters:
        ch["download_url"] = f"/download/{job_id}/{quote(ch['filename'])}"

    return {
        "job_id": job_id,
        "chapter_count": len(chapters),
        "chapters": chapters,
        "download_all_url": f"/download-all/{job_id}",
        "notice": notice,
        "spellcheck": spellcheck_findings.get("spellcheck"),
    }


# ---------------------------------------------------------------- MERGE ---

@app.post("/merge")
async def merge(request: Request):
    """Merges multiple uploaded PDF files in user-specified order."""
    data, files, _ = await _parse_request_data(request)
    if not files:
        raise HTTPException(status_code=400, detail="Upload two or more PDFs to merge.")

    job_id, job_output_dir = _new_job()
    saved_paths = []
    for i, f in enumerate(files):
        ext = os.path.splitext(f.filename or "")[1] or ".pdf"
        p = UPLOAD_DIR / f"{job_id}_{i:03d}{ext}"
        with open(p, "wb") as dst:
            shutil.copyfileobj(f.file, dst, length=1024 * 1024)
        saved_paths.append(str(p))

    output_name = data.get("output_name", "Merged.pdf")

    try:
        results = await run_in_threadpool(
            merge_pdfs,
            saved_paths,
            str(job_output_dir),
            output_name=output_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Merge failed: {exc}")
    finally:
        gc.collect()

    for r in results:
        r["download_url"] = f"/download/{job_id}/{quote(r['filename'])}"

    return {
        "job_id": job_id,
        "chapter_count": len(results),
        "chapters": results,
        "download_all_url": f"/download-all/{job_id}",
    }


# -------------------------------------------------------------- CONVERT ---

@app.post("/convert")
async def convert(request: Request):
    """
    Converts between formats:
    - image_to_pdf (multiple images -> 1 PDF)
    - pdf_to_image (PDF -> JPG / PNG)
    - pdf_to_word (PDF -> DOCX)
    - word_to_pdf (DOCX -> PDF)
    """
    data, files, file = await _parse_request_data(request)
    job_id, job_output_dir = _new_job()
    conv_type = data.get("type", "")

    try:
        if conv_type == "image_to_pdf":
            if not files:
                raise HTTPException(status_code=400, detail="Upload one or more images to combine.")
            saved_paths = []
            for i, f in enumerate(files):
                ext = os.path.splitext(f.filename or "")[1] or ".jpg"
                p = UPLOAD_DIR / f"{job_id}_{i:03d}{ext}"
                with open(p, "wb") as dst:
                    shutil.copyfileobj(f.file, dst, length=1024 * 1024)
                saved_paths.append(str(p))
            results = await run_in_threadpool(images_to_pdf, saved_paths, str(job_output_dir))

        elif conv_type == "pdf_to_image":
            job_input_path, _ = await _fetch_one_input(data, file, "convert")
            fmt = data.get("format", "jpg")
            results = await run_in_threadpool(pdf_to_images, str(job_input_path), str(job_output_dir), fmt=fmt)

        elif conv_type == "pdf_to_word":
            job_input_path, orig_name = await _fetch_one_input(data, file, "convert")
            base_name = os.path.splitext(orig_name)[0]
            results = await run_in_threadpool(pdf_to_word, str(job_input_path), str(job_output_dir), output_name=base_name)

        elif conv_type == "word_to_pdf":
            job_input_path, orig_name = await _fetch_one_input(data, file, "convert")
            results = await run_in_threadpool(word_to_pdf, str(job_input_path), str(job_output_dir), output_name=orig_name)

        else:
            raise HTTPException(status_code=400, detail=f'Unknown conversion type "{conv_type}".')

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion failed: {exc}")
    finally:
        gc.collect()

    for r in results:
        r["download_url"] = f"/download/{job_id}/{quote(r['filename'])}"

    return {
        "job_id": job_id,
        "chapter_count": len(results),
        "chapters": results,
        "download_all_url": f"/download-all/{job_id}",
    }


# ------------------------------------------------------------- COMPRESS ---

@app.post("/compress")
async def compress_endpoint(request: Request):
    """
    Compresses an uploaded PDF, pre-uploaded chunked PDF (via job_id), or remote URL.
    Keeps vector text and fonts razor-sharp while deflating bloated streams (<100MB guaranteed).
    Returns both compressor stats and the chapter card format expected by the frontend.
    """
    data, _, file = await _parse_request_data(request)
    job_id = data.get("job_id")
    mode = data.get("mode") or "visually_lossless"
    try:
        quality = int(data.get("image_quality") or 90)
    except (ValueError, TypeError):
        quality = 90

    # Clean Swagger UI placeholders
    if job_id and str(job_id).strip().lower() in ("string", "null", "undefined", "none", ""):
        job_id = None

    # Check if this job_id was already compressed during chunked upload assembly
    if job_id and _valid_job_id(job_id):
        job_output_dir = OUTPUT_DIR / job_id
        stats_file = job_output_dir / "compress_stats.json"
        if stats_file.is_file():
            try:
                with open(stats_file, encoding="utf-8") as sf:
                    cached = json.load(sf)
                out_name = cached.get("filename") or "compressed_document.pdf"
                if (job_output_dir / out_name).is_file():
                    cached["chapters"] = [
                        {
                            "title": f"Compressed: {out_name.replace('compressed_', '', 1)}",
                            "filename": out_name,
                            "pages": f"{cached.get('original_size_mb')} MB → {cached.get('compressed_size_mb')} MB ({cached.get('savings_percent')}% saved)",
                            "download_url": f"/download/{job_id}/{quote(out_name)}",
                        }
                    ]
                    cached["chapter_count"] = 1
                    cached["download_url"] = f"/download/{job_id}/{quote(out_name)}"
                    cached["download_all_url"] = f"/download/{job_id}/{quote(out_name)}"
                    return cached
            except Exception:
                pass

        # If not cached, compress from existing source
        input_path = _find_job_source(job_id)
        orig_name = data.get("filename") or input_path.name
    elif file and file.filename:
        job_id, job_output_dir = _new_job()
        input_path, orig_name = await _fetch_one_input(data, file, "compress")
    elif data.get("pdf_url"):
        job_id, job_output_dir = _new_job()
        input_path, orig_name = await _fetch_one_input(data, file, "compress")
    else:
        raise HTTPException(status_code=400, detail="Provide either a file upload, a 'pdf_url', or a valid 'job_id'.")

    job_output_dir = OUTPUT_DIR / job_id
    job_output_dir.mkdir(parents=True, exist_ok=True)

    clean_name = re.sub(r"^[0-9a-fA-F]{8,12}_(source|compress)\.?", "", orig_name).strip() or "document.pdf"
    if not clean_name.lower().endswith(".pdf"):
        clean_name += ".pdf"
    out_name = clean_name if clean_name.startswith("compressed_") else f"compressed_{clean_name}"
    output_path = job_output_dir / out_name

    try:
        stats = await run_in_threadpool(
            compress_pdf,
            input_path=str(input_path),
            output_path=str(output_path),
            mode=mode,
            image_quality=quality,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Compression failed: {exc}")

    orig_mb = stats["original_size_mb"]
    comp_mb = stats["compressed_size_mb"]
    saved_mb = stats["saved_mb"]
    saved_pct = stats["savings_percent"]
    duration = stats["duration_seconds"]

    stats["job_id"] = job_id
    stats["filename"] = out_name
    stats["download_url"] = f"/download/{job_id}/{quote(out_name)}"
    stats["download_all_url"] = f"/download/{job_id}/{quote(out_name)}"
    stats["chapter_count"] = 1
    stats["chapters"] = [
        {
            "title": f"Compressed: {clean_name}",
            "filename": out_name,
            "pages": f"{orig_mb} MB → {comp_mb} MB ({saved_pct}% saved)",
            "download_url": f"/download/{job_id}/{quote(out_name)}",
        }
    ]
    stats["notice"] = (
        f"Compressed {orig_mb} MB down to {comp_mb} MB "
        f"— saved {saved_mb} MB ({saved_pct}%) in {duration}s"
    )

    # Cache stats for fast retrieval
    try:
        with open(job_output_dir / "compress_stats.json", "w", encoding="utf-8") as sf:
            json.dump(stats, sf)
    except Exception:
        pass

    return stats


# ------------------------------------------------------------- DOWNLOAD ---

@app.get("/download/{job_id}/{filename}")
async def download_one(job_id: str, filename: str):
    """Streams an individual output file to the user as attachment."""
    if not _valid_job_id(job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id.")

    safe_name = Path(filename).name
    target_path = OUTPUT_DIR / job_id / safe_name
    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="Requested file was not found.")

    return FileResponse(
        path=str(target_path),
        filename=safe_name,
        media_type="application/octet-stream",
    )


@app.get("/download-all/{job_id}")
async def download_all(job_id: str):
    """Zips all deliverables for a job and streams back as a .zip archive."""
    if not _valid_job_id(job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id.")

    directory = OUTPUT_DIR / job_id
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Job directory not found.")

    zip_path = OUTPUT_DIR / f"{job_id}.zip"

    def _create_zip():
        with zipfile.ZipFile(str(zip_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(str(directory)):
                dirs[:] = [d for d in dirs if d != "thumbs"]
                for fname in sorted(files):
                    if fname in ("sections.json", "compress_stats.json"):
                        continue
                    full_p = os.path.join(root, fname)
                    arcname = os.path.relpath(full_p, str(directory))
                    zf.write(full_p, arcname=arcname)

    await run_in_threadpool(_create_zip)

    if not zip_path.is_file():
        raise HTTPException(status_code=500, detail="Failed to build zip archive.")

    return FileResponse(
        path=str(zip_path),
        filename=f"{job_id}.zip",
        media_type="application/zip",
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("fastapi_app:app", host="0.0.0.0", port=port, reload=True)
