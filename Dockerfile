# Root-level build for graders pointing `docker build` at the repository root
# (scripts/run_docker_submission.py expects a Dockerfile here). Mirrors
# solution/Dockerfile with paths prefixed.
FROM python:3.11-slim

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
