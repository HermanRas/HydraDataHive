# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (better layer caching)
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code
COPY app/ /app/app/
COPY cli.py /app/
COPY tests/ /app/tests/

# Non-root user
RUN useradd --create-home --uid 1000 hydra \
    && mkdir -p /data/input /data/datastore /data/out /keys \
    && chown -R hydra:hydra /app /data /keys
USER hydra

VOLUME ["/data", "/keys"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/v1/identity',timeout=2).status==200 else 1)" || exit 1

ENTRYPOINT ["python", "-m", "app"]