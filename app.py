"""
app.py
------
Flask backend for URL-triggered chapter-wise PDF splitting.

Endpoints:
  GET  /                     -> UI (paste a PDF URL, click Split)
  POST /split                -> body: {"pdf_url": "..."} OR a file upload
                                 downloads the PDF, splits it, returns JSON
                                 with a download link per chapter
  GET  /download/<job_id>/<filename>  -> download one chapter PDF
  GET  /download-all/<job_id>         -> download all chapters as a .zip

Each "split" is a job_id (uuid) so multiple users/PDFs don't collide.
Plug this into your existing pdf-textbook-extractor Flask app: copy
pdf_splitter.py + the /split, /download routes in, and reuse your
existing upload handling if you already have one.
"""

import os
import uuid
import zipfile
import requests
from flask import Flask, request, jsonify, send_from_directory, render_template

from pdf_splitter import split_pdf_by_unit, split_pdf_by_lesson, split_pdf_combined

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB cap


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/split", methods=["POST"])
def split():
    job_id = uuid.uuid4().hex[:12]
    job_input_path = os.path.join(UPLOAD_DIR, f"{job_id}.pdf")
    job_output_dir = os.path.join(OUTPUT_DIR, job_id)

    # Case 1: PDF stored somewhere and referenced by URL
    if request.is_json and request.json.get("pdf_url"):
        pdf_url = request.json["pdf_url"]
        try:
            resp = requests.get(pdf_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            return jsonify({"error": f"Could not fetch PDF from URL: {e}"}), 400
        with open(job_input_path, "wb") as f:
            f.write(resp.content)

    # Case 2: PDF uploaded directly as multipart/form-data
    elif "file" in request.files:
        request.files["file"].save(job_input_path)

    else:
        return jsonify({"error": "Provide either a 'pdf_url' (JSON) or a 'file' (multipart)."}), 400

    if request.is_json:
        body = request.json or {}
        mode = body.get("mode", "unit")
        keyword = body.get("keyword", "Unit")
    else:
        mode = request.values.get("mode", "unit")
        keyword = request.values.get("keyword", "Unit")

    try:
        if mode == "lesson":
            chapters = split_pdf_by_lesson(job_input_path, job_output_dir)
        elif mode == "combined":
            chapters = split_pdf_combined(job_input_path, job_output_dir, keyword=keyword)
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
        for root, _dirs, files in os.walk(directory):
            for fname in files:
                full_path = os.path.join(root, fname)
                arcname = os.path.relpath(full_path, directory)  # keeps subfolders in the zip
                zf.write(full_path, arcname=arcname)

    return send_from_directory(OUTPUT_DIR, f"{job_id}.zip", as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
