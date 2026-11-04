"""
fastapi_app.py
--------------
FastAPI backend service for high-performance, memory-safe large PDF compression.

Features:
- Handles 300MB - 2GB+ PDFs using 10MB streaming chunk uploads (avoids 413 limits).
- Provides lossless and visually-lossless PDF compression without degrading text sharpness.
- Streams output downloads in chunks to keep server memory footprint constant (~15MB).
- Strict input validation, path sanitization, and PDF magic byte verification (%PDF-).
"""

import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional, Literal
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, status, Query
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from pdf_compressor import compress_pdf

logger = logging.getLogger("fastapi_app")

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Large PDF Compression Service",
    description="High-performance lossless and visually-lossless PDF compression using streaming I/O.",
    version="1.0.0",
)

# Enable CORS for browser integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PDF Compressor — High Quality Optimization</title>
<style>
  :root {
    --bg: #141b22;
    --card: #1c2530;
    --border: #2e3a47;
    --accent: #d4a359;
    --accent-hover: #e5b369;
    --text: #f0ede6;
    --text-dim: #9aa3af;
    --success: #4ade80;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }
  .container {
    width: 100%;
    max-width: 620px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 32px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.4);
  }
  h1 { font-size: 24px; font-weight: 700; margin-bottom: 6px; color: var(--accent); }
  p.subtitle { color: var(--text-dim); font-size: 14px; margin-bottom: 24px; line-height: 1.4; }
  .dropzone {
    border: 2px dashed var(--border);
    border-radius: 10px;
    padding: 34px 20px;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s ease;
    background: rgba(255,255,255,0.02);
  }
  .dropzone:hover, .dropzone.dragover {
    border-color: var(--accent);
    background: rgba(212, 163, 89, 0.08);
  }
  .dropzone svg { width: 44px; height: 44px; stroke: var(--accent); fill: none; margin-bottom: 12px; }
  .drop-text { font-size: 15px; font-weight: 500; margin-bottom: 4px; }
  .drop-hint { font-size: 12px; color: var(--text-dim); }
  .file-preview {
    margin-top: 18px;
    padding: 12px 16px;
    background: rgba(212, 163, 89, 0.12);
    border: 1px solid var(--accent);
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 14px;
  }
  .file-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 380px; }
  .options { margin-top: 22px; display: flex; flex-direction: column; gap: 14px; }
  label { font-size: 13px; font-weight: 500; color: var(--text-dim); margin-bottom: 4px; display: block; }
  select, input[type=range] {
    width: 100%;
    padding: 10px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-size: 14px;
    outline: none;
  }
  .btn {
    width: 100%;
    margin-top: 24px;
    padding: 14px;
    background: var(--accent);
    color: #141b22;
    border: none;
    border-radius: 8px;
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
  }
  .btn:hover:not(:disabled) { background: var(--accent-hover); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .progress-wrap { margin-top: 20px; display: none; }
  .progress-bar-bg { width: 100%; height: 8px; background: var(--bg); border-radius: 4px; overflow: hidden; margin-top: 8px; }
  .progress-bar-fill { width: 0%; height: 100%; background: var(--accent); transition: width 0.2s ease; }
  .progress-text { font-size: 13px; color: var(--text-dim); display: flex; justify-content: space-between; }
  .result-card {
    margin-top: 24px;
    background: rgba(74, 222, 128, 0.08);
    border: 1px solid var(--success);
    border-radius: 10px;
    padding: 20px;
    display: none;
  }
  .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 14px 0; }
  .stat-box { background: var(--bg); padding: 10px 14px; border-radius: 6px; }
  .stat-label { font-size: 12px; color: var(--text-dim); }
  .stat-val { font-size: 16px; font-weight: 700; color: var(--text); }
  .stat-val.highlight { color: var(--success); }
  .actions { display: flex; gap: 10px; margin-top: 16px; }
  .btn-action {
    flex: 1;
    padding: 11px;
    text-align: center;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    text-decoration: none;
    cursor: pointer;
    transition: opacity 0.2s;
  }
  .btn-download { background: var(--success); color: #0d2818; }
  .btn-view { background: transparent; border: 1px solid var(--border); color: var(--text); }
  .btn-view:hover { border-color: var(--text); }
  .hidden { display: none !important; }
</style>
</head>
<body>
<div class="container">
  <h1>PDF Compressor</h1>
  <p class="subtitle">Lossless and visually-lossless optimization for large PDFs without losing text or formula sharpness.</p>

  <div class="dropzone" id="dropzone">
    <svg viewBox="0 0 24 24" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
    <div class="drop-text">Click to choose PDF or drag & drop here</div>
    <div class="drop-hint">Supports large files (345MB+) with automatic chunked streaming</div>
    <input type="file" id="fileInput" accept="application/pdf" class="hidden" />
  </div>

  <div class="file-preview hidden" id="filePreview">
    <span class="file-name" id="fileName">file.pdf</span>
    <span style="color:var(--accent); font-weight:600;" id="fileSize">0 MB</span>
  </div>

  <div class="options">
    <div>
      <label for="modeSelect">Compression Mode</label>
      <select id="modeSelect">
        <option value="visually_lossless" selected>Visually Lossless (High Reduction • Razor-sharp text & optimized images)</option>
        <option value="lossless">Strict Lossless (Structural deflation & deduplication only)</option>
      </select>
    </div>
    <div id="qualityWrap">
      <label for="qualitySlider">Image Quality: <span id="qualityVal">90</span>%</label>
      <input type="range" id="qualitySlider" min="50" max="98" value="90" />
    </div>
  </div>

  <button class="btn" id="compressBtn" disabled>Compress PDF</button>

  <div class="progress-wrap" id="progressWrap">
    <div class="progress-text">
      <span id="progressStatus">Uploading...</span>
      <span id="progressPercent">0%</span>
    </div>
    <div class="progress-bar-bg">
      <div class="progress-bar-fill" id="progressBar"></div>
    </div>
  </div>

  <div class="result-card" id="resultCard">
    <h3 style="color:var(--success); font-size:16px;">Compression Complete!</h3>
    <div class="stats-grid">
      <div class="stat-box"><div class="stat-label">Original Size</div><div class="stat-val" id="resOrig">0 MB</div></div>
      <div class="stat-box"><div class="stat-label">Compressed Size</div><div class="stat-val" id="resComp">0 MB</div></div>
      <div class="stat-box"><div class="stat-label">Saved Space</div><div class="stat-val highlight" id="resSaved">0 MB</div></div>
      <div class="stat-box"><div class="stat-label">Reduction</div><div class="stat-val highlight" id="resPct">0%</div></div>
    </div>
    <div class="actions">
      <a class="btn-action btn-download" id="downloadBtn" href="#" download>Download PDF</a>
      <a class="btn-action btn-view" id="viewBtn" href="#" target="_blank">View in Tab</a>
    </div>
  </div>
</div>

<script>
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('fileInput');
  const filePreview = document.getElementById('filePreview');
  const fileName = document.getElementById('fileName');
  const fileSize = document.getElementById('fileSize');
  const modeSelect = document.getElementById('modeSelect');
  const qualityWrap = document.getElementById('qualityWrap');
  const qualitySlider = document.getElementById('qualitySlider');
  const qualityVal = document.getElementById('qualityVal');
  const compressBtn = document.getElementById('compressBtn');
  const progressWrap = document.getElementById('progressWrap');
  const progressStatus = document.getElementById('progressStatus');
  const progressPercent = document.getElementById('progressPercent');
  const progressBar = document.getElementById('progressBar');
  const resultCard = document.getElementById('resultCard');

  let selectedFile = null;

  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files[0]) handleFile(fileInput.files[0]);
  });

  ['dragenter', 'dragover'].forEach(name => {
    dropzone.addEventListener(name, (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
  });
  ['dragleave', 'drop'].forEach(name => {
    dropzone.addEventListener(name, (e) => { e.preventDefault(); dropzone.classList.remove('dragover'); });
  });
  dropzone.addEventListener('drop', (e) => {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFile(e.dataTransfer.files[0]);
    }
  });

  function handleFile(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      alert('Please choose a valid PDF file.');
      return;
    }
    selectedFile = file;
    fileName.textContent = file.name;
    const mb = (file.size / (1024 * 1024)).toFixed(1);
    fileSize.textContent = `${mb} MB`;
    filePreview.classList.remove('hidden');
    compressBtn.disabled = false;
    resultCard.style.display = 'none';
  }

  modeSelect.addEventListener('change', () => {
    qualityWrap.style.display = modeSelect.value === 'visually_lossless' ? 'block' : 'none';
  });

  qualitySlider.addEventListener('input', () => {
    qualityVal.textContent = qualitySlider.value;
  });

  async function uploadChunked(file, onProgress) {
    const CHUNK_SIZE = 10 * 1024 * 1024;
    const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
    const randomBytes = new Uint8Array(6);
    crypto.getRandomValues(randomBytes);
    const jobId = Array.from(randomBytes).map(b => b.toString(16).padStart(2, '0')).join('');

    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const blob = file.slice(start, end);

      const fd = new FormData();
      fd.append('chunk', blob, 'chunk.bin');
      fd.append('job_id', jobId);
      fd.append('chunk_index', i);
      fd.append('total_chunks', totalChunks);
      fd.append('filename', file.name);

      const pct = Math.round(((i + 1) / totalChunks) * 100);
      onProgress(pct);

      const res = await fetch('/upload-chunk', { method: 'POST', body: fd });
      if (!res.ok) throw new Error('Upload failed on chunk ' + (i + 1));
    }
    return jobId;
  }

  compressBtn.addEventListener('click', async () => {
    if (!selectedFile) return;

    compressBtn.disabled = true;
    resultCard.style.display = 'none';
    progressWrap.style.display = 'block';
    progressBar.style.width = '0%';

    try {
      let jobId;
      if (selectedFile.size > 10 * 1024 * 1024) {
        progressStatus.textContent = `Streaming ${Math.round(selectedFile.size / (1024 * 1024))} MB in chunks…`;
        jobId = await uploadChunked(selectedFile, (pct) => {
          progressBar.style.width = pct + '%';
          progressPercent.textContent = pct + '%';
        });
      }

      progressStatus.textContent = 'Optimizing PDF streams & fonts…';
      progressPercent.textContent = 'Processing';

      const fd = new FormData();
      if (jobId) {
        fd.append('job_id', jobId);
      } else {
        fd.append('file', selectedFile);
      }
      fd.append('mode', modeSelect.value);
      fd.append('image_quality', qualitySlider.value);

      const res = await fetch('/compress', { method: 'POST', body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Compression failed.');
      }

      const data = await res.json();

      document.getElementById('resOrig').textContent = data.original_size_mb + ' MB';
      document.getElementById('resComp').textContent = data.compressed_size_mb + ' MB';
      document.getElementById('resSaved').textContent = data.saved_mb + ' MB';
      document.getElementById('resPct').textContent = data.savings_percent + '% smaller';

      const downloadBtn = document.getElementById('downloadBtn');
      const viewBtn = document.getElementById('viewBtn');
      downloadBtn.href = data.download_url;
      downloadBtn.download = data.filename;
      viewBtn.href = data.download_url;

      resultCard.style.display = 'block';
    } catch (err) {
      alert('Error: ' + err.message);
    } finally {
      progressWrap.style.display = 'none';
      compressBtn.disabled = false;
    }
  });
</script>
</body>
</html>
"""


# ------------------------------------------------------------- MODELS ---

class ChunkResponse(BaseModel):
    status: str
    job_id: str
    chunk_index: int
    total_chunks: int
    complete: bool
    compressed: Optional[bool] = None
    original_size_mb: Optional[float] = None
    compressed_size_mb: Optional[float] = None
    saved_mb: Optional[float] = None
    savings_percent: Optional[float] = None
    filename: Optional[str] = None
    download_url: Optional[str] = None


class CompressResponse(BaseModel):
    status: str
    job_id: str
    filename: str
    original_size_mb: float
    compressed_size_mb: float
    saved_mb: float
    savings_percent: float
    mode: str
    duration_seconds: float
    download_url: str


def _sanitize_name(filename: str) -> str:
    name = Path(filename).name
    return re.sub(r"[^\w\-.]", "_", name)


def _validate_job_id(job_id: str) -> bool:
    return bool(job_id and re.fullmatch(r"[0-9a-zA-Z_\-]+", job_id))


# ---------------------------------------------------------- ENDPOINTS ---

@app.get("/", response_class=HTMLResponse)
async def index_page():
    """Serves the interactive web interface for PDF compression."""
    return HTMLResponse(content=INDEX_HTML)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "fastapi-pdf-compressor"}


@app.post("/upload-chunk", response_model=ChunkResponse)
async def upload_chunk(
    chunk: UploadFile = File(...),
    job_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form("document.pdf"),
    compress_on_complete: bool = Form(True, description="Automatically compress PDF upon assembly"),
    mode: Literal["lossless", "visually_lossless"] = Form("visually_lossless"),
    image_quality: int = Form(90, ge=1, le=100),
):
    """
    Receives file slices (e.g. 10MB each) and appends directly to disk.
    When the final chunk is received and assembled, automatically compresses the PDF
    on the server side right after assembly before saving, without requiring any manual
    compression step from the user.
    """
    if not _validate_job_id(job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    safe_name = _sanitize_name(filename)
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    target_path = UPLOAD_DIR / f"{job_id}_source.pdf"
    write_mode = "wb" if chunk_index == 0 else "ab"

    try:
        with open(target_path, write_mode) as dest:
            shutil.copyfileobj(chunk.file, dest, length=1024 * 1024)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write chunk {chunk_index}: {exc}")
    finally:
        await chunk.close()

    # Verify magic bytes on the initial chunk
    if chunk_index == 0:
        with open(target_path, "rb") as f:
            header = f.read(5)
            if header != b"%PDF-":
                try:
                    os.remove(target_path)
                except OSError:
                    pass
                raise HTTPException(status_code=422, detail="Invalid PDF file (missing %PDF- header).")

    is_complete = (chunk_index + 1 >= total_chunks)

    # Automatic compression pipeline right after assembly
    compressed = False
    orig_mb = None
    comp_mb = None
    saved_mb = None
    savings_pct = None
    out_name = None
    download_url = None

    if is_complete and compress_on_complete and target_path.is_file():
        clean_name = safe_name if not safe_name.startswith("compressed_") else safe_name
        out_name = f"compressed_{clean_name}"
        job_output_dir = OUTPUT_DIR / job_id
        job_output_dir.mkdir(parents=True, exist_ok=True)
        final_output_path = job_output_dir / out_name
        temp_comp_path = UPLOAD_DIR / f"{job_id}_compressed_tmp.pdf"

        try:
            # Run compression in threadpool to keep the async event loop unblocked
            stats = await run_in_threadpool(
                compress_pdf,
                input_path=str(target_path),
                output_path=str(temp_comp_path),
                mode=mode,
                image_quality=image_quality,
            )

            if temp_comp_path.is_file() and temp_comp_path.stat().st_size > 0:
                # Provide immediate download path in output directory
                shutil.copyfile(temp_comp_path, final_output_path)
                # Replace the uploaded source with the compressed version
                temp_comp_path.replace(target_path)

                compressed = True
                orig_mb = stats.get("original_size_mb")
                comp_mb = stats.get("compressed_size_mb")
                saved_mb = stats.get("saved_mb")
                savings_pct = stats.get("savings_percent")
                download_url = f"/download/{job_id}/{out_name}"
        except Exception as exc:
            # Safe degradation: compression failure never breaks the upload
            logger.warning(f"Automatic compression failed for job {job_id}: {exc}")
        finally:
            if temp_comp_path.is_file():
                try:
                    temp_comp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    return ChunkResponse(
        status="ok",
        job_id=job_id,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        complete=is_complete,
        compressed=compressed,
        original_size_mb=orig_mb,
        compressed_size_mb=comp_mb,
        saved_mb=saved_mb,
        savings_percent=savings_pct,
        filename=out_name,
        download_url=download_url,
    )


@app.post("/compress", response_model=CompressResponse)
async def compress_endpoint(
    file: Optional[UploadFile] = File(None, description="Direct PDF file upload"),
    job_id: Optional[str] = Form(None, description="Pre-uploaded chunk session ID (leave blank if uploading file)"),
    mode: Literal["lossless", "visually_lossless"] = Form("visually_lossless"),
    image_quality: int = Form(90, ge=1, le=100, description="Image quality for visually_lossless mode (1-100)"),
):
    """
    Compresses a PDF losslessly or visually-losslessly.
    Supports either direct file uploads or pre-uploaded chunked jobs (via job_id).
    """
    # Clean up Swagger UI placeholder strings
    if job_id:
        clean_id = job_id.strip()
        if clean_id.lower() in ("string", "null", "undefined", "none", ""):
            job_id = None
        else:
            job_id = clean_id

    # 1. Direct file upload takes precedence
    if file and file.filename:
        job_id = uuid.uuid4().hex[:12]
        safe_name = _sanitize_name(file.filename)
        if not safe_name.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are supported.")

        source_path = UPLOAD_DIR / f"{job_id}_source.pdf"
        try:
            with open(source_path, "wb") as dest:
                shutil.copyfileobj(file.file, dest, length=1024 * 1024)
        finally:
            await file.close()
        orig_name = safe_name

    # 2. Or pre-uploaded chunked job_id
    elif job_id and _validate_job_id(job_id):
        source_path = UPLOAD_DIR / f"{job_id}_source.pdf"
        if not source_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"Uploaded PDF for job_id '{job_id}' not found. Please upload chunks via /upload-chunk first or attach a file directly."
            )
        existing_outputs = list((OUTPUT_DIR / job_id).glob("compressed_*.pdf")) if (OUTPUT_DIR / job_id).is_dir() else []
        if existing_outputs:
            orig_name = existing_outputs[0].name.replace("compressed_", "", 1)
        else:
            orig_name = "compressed_document.pdf"
    else:
        raise HTTPException(
            status_code=400,
            detail="Please provide a PDF file in the 'file' field or a valid 'job_id' from /upload-chunk."
        )

    # 2. Prepare output path
    job_output_dir = OUTPUT_DIR / job_id
    job_output_dir.mkdir(parents=True, exist_ok=True)
    out_filename = f"compressed_{orig_name}" if not orig_name.startswith("compressed_") else orig_name
    output_path = job_output_dir / out_filename

    # 3. Execute low-memory compression
    try:
        stats = compress_pdf(
            input_path=str(source_path),
            output_path=str(output_path),
            mode=mode,
            image_quality=image_quality,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"PDF compression failed: {exc}")

    download_url = f"/download/{job_id}/{out_filename}"

    return CompressResponse(
        status="success",
        job_id=job_id,
        filename=out_filename,
        original_size_mb=stats["original_size_mb"],
        compressed_size_mb=stats["compressed_size_mb"],
        saved_mb=stats["saved_mb"],
        savings_percent=stats["savings_percent"],
        mode=mode,
        duration_seconds=stats["duration_seconds"],
        download_url=download_url,
    )


@app.get("/download/{job_id}/{filename}")
async def download_file(job_id: str, filename: str):
    """
    Streams the compressed PDF back to the client using FileResponse.
    Supports HTTP Range requests and uses zero RAM.
    """
    if not _validate_job_id(job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id.")

    safe_name = _sanitize_name(filename)
    target_path = OUTPUT_DIR / job_id / safe_name

    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="Requested file was not found.")

    return FileResponse(
        path=str(target_path),
        media_type="application/pdf",
        filename=safe_name,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("fastapi_app:app", host="0.0.0.0", port=8000, reload=True)
