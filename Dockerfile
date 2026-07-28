# Root-level build for graders pointing `docker build` at the repository root
# (scripts/run_docker_submission.py expects a Dockerfile here). Mirrors
# solution/Dockerfile with paths prefixed.
#
# Base pin must stay in lockstep with solution/Dockerfile: python:3.11-slim is a
# moving tag that recently jumped to trixie, silently swapping Tesseract 5.3 for
# 5.5. Every OCR threshold here was tuned against 5.3, and the full-train A/B
# measures 126.10 (bookworm, 5.3.0) vs 126.04 (trixie, 5.5.0). This is the file
# graders build, so an unpinned tag here would drift the graded artifact.
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY solution/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY solution/solution.py solution/run.sh /app/
COPY solution/mib_pipeline /app/mib_pipeline
RUN chmod +x /app/run.sh

ENV PYTHONDONTWRITEBYTECODE=1 \
    OMP_THREAD_LIMIT=1 \
    OMP_NUM_THREADS=1 \
    TESSERACT_NUM_THREADS=1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/run.sh"]
