FROM python:3.11-slim

# Offline OCR + PDF tooling. tesseract-ocr provides the OCR engine used as a
# fallback for scanned/rasterized pages; no network is needed at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY solution.py run.sh /app/
COPY mib_pipeline /app/mib_pipeline
RUN chmod +x /app/run.sh

# Keep native math libs single-threaded; parallelism is at the packet level.
ENV OMP_THREAD_LIMIT=1 \
    OMP_NUM_THREADS=1 \
    TESSERACT_NUM_THREADS=1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/run.sh"]
