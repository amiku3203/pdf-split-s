"""
app.py
------
Flask backend for a multi-tool PDF app: Split and Convert.

Endpoints:
  GET  /                       -> UI
  POST /split                  -> body: {"pdf_url": "..."} OR a file upload
                                   modes: "unit" | "lesson" | "combined" |
                                          "range" | "fixed" | "per_page"
  POST /convert                -> body: {"pdf_url": "..."} OR file upload(s)
                                   types: "pdf_to_word" | "pdf_to_image" |
                                          "word_to_pdf" | "image_to_pdf"
  GET  /download/<job_id>/<path:filename>  -> download one output file
  GET  /download-all/<job_id>              -> download all outputs as .zip

Each job gets a uuid so multiple users/files don't collide.
"""

import os
import re
import uuid
import zipfile
import requests
from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS

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
from pdf_converter import pdf_to_images, images_to_pdf, pdf_to_word, word_to_pdf, generate_thumbnails

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 150 * 1024 * 1024  # 150 MB cap

# Allowed origins for CORS (no trailing slashes)
ALLOWED_ORIGINS = [
    "https://crm.smatoroai.com",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:5000",
    "http://127.0.0.1:5173",
    re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"),
]

CORS(
    app,
    resources={r"/*": {"origins": ALLOWED_ORIGINS}},
    supports_credentials=True,
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)


@app.route("/")
def index():
    return render_template("index.html")


def _new_job():
    job_id = uuid.uuid4().hex[:12]
    job_output_dir = os.path.join(OUTPUT_DIR, job_id)
    return job_id, job_output_dir


def _fetch_one_input(prefix="input"):
    """
    Reads either a JSON {"pdf_url": "..."} body or a single-file upload
    ("file" field) and saves it to UPLOAD_DIR. Returns (saved_path, original_basename).
    Raises ValueError with a user-facing message on failure.
    """
    job_tag = uuid.uuid4().hex[:8]

    if request.is_json and request.json.get("pdf_url"):
        url = request.json["pdf_url"]
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ValueError(f"Could not fetch file from URL: {e}")
        original_name = os.path.basename(url.split("?")[0]) or "file.pdf"
        ext = os.path.splitext(original_name)[1] or ".pdf"
        path = os.path.join(UPLOAD_DIR, f"{job_tag}_{prefix}{ext}")
        with open(path, "wb") as f:
            f.write(resp.content)
        return path, original_name

    if "file" in request.files:
        f = request.files["file"]
        original_name = f.filename or "file.pdf"
        ext = os.path.splitext(original_name)[1] or ".pdf"
        path = os.path.join(UPLOAD_DIR, f"{job_tag}_{prefix}{ext}")
        f.save(path)
        return path, original_name

    raise ValueError("Provide either a 'pdf_url' (JSON) or a 'file' (multipart).")


def _save_input_for_job(job_id):
    """
    Like _fetch_one_input, but saves the file under a name keyed by job_id
    (so a later request that only knows the job_id — like /split-pages —
    can find it again). Returns the saved path.
    """
    if request.is_json and request.json.get("pdf_url"):
        url = request.json["pdf_url"]
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ValueError(f"Could not fetch file from URL: {e}")
        ext = os.path.splitext(url.split("?")[0])[1] or ".pdf"
        path = os.path.join(UPLOAD_DIR, f"{job_id}_source{ext}")
        with open(path, "wb") as f:
            f.write(resp.content)
        return path

    if "file" in request.files:
        f = request.files["file"]
        ext = os.path.splitext(f.filename or "")[1] or ".pdf"
        path = os.path.join(UPLOAD_DIR, f"{job_id}_source{ext}")
        f.save(path)
        return path

    raise ValueError("Provide either a 'pdf_url' (JSON) or a 'file' (multipart).")


def _find_job_source(job_id):
    import glob
    matches = glob.glob(os.path.join(UPLOAD_DIR, f"{job_id}_source.*"))
    if not matches:
        raise ValueError("Couldn't find the uploaded file for this job — try loading the pages again.")
    return matches[0]


# -------------------------------------------------------------- PREVIEW ---

@app.route("/preview", methods=["POST"])
def preview():
    """
    Loads a PDF and renders a small thumbnail per page, for the visual
    page picker. Returns {job_id, page_count} — thumbnails are then
    fetched one by one via GET /thumbnail/<job_id>/<n>.
    """
    job_id, job_output_dir = _new_job()
    try:
        source_path = _save_input_for_job(job_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    thumbs_dir = os.path.join(job_output_dir, "thumbs")
    try:
        page_count = generate_thumbnails(source_path, thumbs_dir)
    except Exception as e:
        return jsonify({"error": f"Could not read that PDF: {e}"}), 422

    return jsonify({"job_id": job_id, "page_count": page_count})


@app.route("/thumbnail/<job_id>/<int:page_num>")
def thumbnail(job_id, page_num):
    thumbs_dir = os.path.join(OUTPUT_DIR, job_id, "thumbs")
    fname = f"thumb_{page_num:04d}.jpg"
    return send_from_directory(thumbs_dir, fname)


@app.route("/split-pages", methods=["POST"])
def split_pages():
    """
    Splits the previously-previewed PDF (found by job_id) down to just the
    pages the user picked in the thumbnail grid.
    body: {"job_id": "...", "pages": [1,2,5,6], "combine": true|false}
    """
    body = request.get_json(force=True, silent=True) or {}
    job_id = body.get("job_id")
    pages = body.get("pages") or []
    combine = bool(body.get("combine"))

    if not job_id:
        return jsonify({"error": "Missing job_id — load the pages first."}), 400

    try:
        source_path = _find_job_source(job_id)
        pages = [int(p) for p in pages]
        job_output_dir = os.path.join(OUTPUT_DIR, job_id)
        results = extract_selected_pages(source_path, job_output_dir, pages, combine=combine)
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    for r in results:
        r["download_url"] = f"/download/{job_id}/{r['filename']}"

    return jsonify({
        "job_id": job_id,
        "chapter_count": len(results),
        "chapters": results,
        "download_all_url": f"/download-all/{job_id}",
    })


# ---------------------------------------------------------------- SPLIT ---

@app.route("/split", methods=["POST"])
def split():
    job_id, job_output_dir = _new_job()

    try:
        job_input_path, _orig_name = _fetch_one_input("split")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if request.is_json:
        body = request.json or {}
        mode = body.get("mode", "unit")
        keyword = body.get("keyword", "Unit")
        ranges_str = body.get("ranges", "")
        pages_per_chunk = body.get("pages_per_chunk", 5)
    else:
        mode = request.values.get("mode", "unit")
        keyword = request.values.get("keyword", "Unit")
        ranges_str = request.values.get("ranges", "")
        pages_per_chunk = request.values.get("pages_per_chunk", 5)

    try:
        pages_per_chunk = int(pages_per_chunk)
    except (TypeError, ValueError):
        pages_per_chunk = 5

    try:
        if mode == "lesson":
            chapters = split_pdf_by_lesson(job_input_path, job_output_dir)
        elif mode == "combined":
            chapters = split_pdf_combined(job_input_path, job_output_dir, keyword=keyword)
        elif mode == "unit_and_lesson":
            chapters = split_pdf_unit_and_lesson(job_input_path, job_output_dir, keyword=keyword)
        elif mode == "extract_text":
            chapters = extract_text_hierarchy(job_input_path, job_output_dir, keyword=keyword)
        elif mode == "combined_with_text":
            chapters = split_pdf_combined_with_text(job_input_path, job_output_dir, keyword=keyword)
        elif mode == "range":
            chapters = split_pdf_by_range(job_input_path, job_output_dir, ranges_str)
        elif mode == "fixed":
            chapters = split_pdf_fixed(job_input_path, job_output_dir, pages_per_chunk)
        elif mode == "per_page":
            chapters = split_pdf_per_page(job_input_path, job_output_dir)
        else:
            chapters = split_pdf_by_unit(job_input_path, job_output_dir, keyword=keyword)
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    for ch in chapters:
        ch["download_url"] = f"/download/{job_id}/{ch['filename']}"

    return jsonify({
        "job_id": job_id,
        "chapter_count": len(chapters),
        "chapters": chapters,
        "download_all_url": f"/download-all/{job_id}",
    })


# ---------------------------------------------------------------- MERGE ---

@app.route("/merge", methods=["POST"])
def merge():
    job_id, job_output_dir = _new_job()

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "Upload two or more PDFs to merge."}), 400

    saved_paths = []
    for i, f in enumerate(files):
        ext = os.path.splitext(f.filename or "")[1] or ".pdf"
        path = os.path.join(UPLOAD_DIR, f"{job_id}_{i:03d}{ext}")
        f.save(path)
        saved_paths.append(path)

    output_name = request.values.get("output_name", "Merged.pdf")

    try:
        results = merge_pdfs(saved_paths, job_output_dir, output_name=output_name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    for r in results:
        r["download_url"] = f"/download/{job_id}/{r['filename']}"

    return jsonify({
        "job_id": job_id,
        "chapter_count": len(results),
        "chapters": results,
        "download_all_url": f"/download-all/{job_id}",
    })


# -------------------------------------------------------------- CONVERT ---

@app.route("/convert", methods=["POST"])
def convert():
    job_id, job_output_dir = _new_job()
    conv_type = request.values.get("type", "")

    try:
        if conv_type == "image_to_pdf":
            # multiple images -> one PDF
            files = request.files.getlist("files")
            if not files:
                return jsonify({"error": "Upload one or more images to combine."}), 400
            saved_paths = []
            for i, f in enumerate(files):
                ext = os.path.splitext(f.filename or "")[1] or ".jpg"
                path = os.path.join(UPLOAD_DIR, f"{job_id}_{i:03d}{ext}")
                f.save(path)
                saved_paths.append(path)
            results = images_to_pdf(saved_paths, job_output_dir)

        elif conv_type == "pdf_to_image":
            job_input_path, _orig_name = _fetch_one_input("convert")
            fmt = request.values.get("format", "jpg")
            results = pdf_to_images(job_input_path, job_output_dir, fmt=fmt)

        elif conv_type == "pdf_to_word":
            job_input_path, orig_name = _fetch_one_input("convert")
            base_name = os.path.splitext(orig_name)[0]
            results = pdf_to_word(job_input_path, job_output_dir, output_name=base_name)

        elif conv_type == "word_to_pdf":
            job_input_path, orig_name = _fetch_one_input("convert")
            results = word_to_pdf(job_input_path, job_output_dir, output_name=orig_name)

        else:
            return jsonify({"error": f'Unknown conversion type "{conv_type}".'}), 400

    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    for r in results:
        r["download_url"] = f"/download/{job_id}/{r['filename']}"

    return jsonify({
        "job_id": job_id,
        "chapter_count": len(results),
        "chapters": results,
        "download_all_url": f"/download-all/{job_id}",
    })


# ------------------------------------------------------------- DOWNLOAD ---

@app.route("/download/<job_id>/<path:filename>")
def download_one(job_id, filename):
    directory = os.path.join(OUTPUT_DIR, job_id)
    return send_from_directory(directory, filename, as_attachment=True)


@app.route("/download-all/<job_id>")
def download_all(job_id):
    directory = os.path.join(OUTPUT_DIR, job_id)
    if not os.path.isdir(directory):
        return jsonify({"error": "Unknown job_id"}), 404

    zip_path = os.path.join(OUTPUT_DIR, f"{job_id}.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d != "thumbs"]  # thumbnails are for preview only
            for fname in files:
                full_path = os.path.join(root, fname)
                arcname = os.path.relpath(full_path, directory)  # keeps subfolders in the zip
                zf.write(full_path, arcname=arcname)

    return send_from_directory(OUTPUT_DIR, f"{job_id}.zip", as_attachment=True)


if __name__ == "__main__":
    # Render (and most hosts) inject the port via the PORT env var and
    # require binding to 0.0.0.0, not 127.0.0.1. debug=False for production.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
