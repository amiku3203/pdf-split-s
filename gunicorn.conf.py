# gunicorn.conf.py
"""
Gunicorn configuration for PDF Toolkit.
Increases request timeouts, payload limits, and memory safety
to cleanly handle large files (including 345MB+ PDFs).
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', 5000)}"
workers = int(os.environ.get("WEB_CONCURRENCY", 2))
threads = int(os.environ.get("PYTHON_THREADS", 2))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 600))  # 10 minutes timeout for heavy PDF tasks
keepalive = 15
max_requests = 100
max_requests_jitter = 20
limit_request_line = 8190
limit_request_field_size = 32768
limit_request_fields = 100

