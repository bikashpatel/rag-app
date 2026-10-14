# Pinned by digest to a build at least two weeks old; bump it deliberately.
FROM python:3.13.15-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# requirements.txt is a hashed lock generated from requirements.in, so nothing is resolved at build time.
COPY requirements.txt .
RUN pip install --require-hashes --no-deps -r requirements.txt && pip check

COPY app ./app

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /data
USER appuser

EXPOSE 8000
# On SIGTERM, open answer streams get 15 s, then uvicorn cancels them (instead of waiting indefinitely).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "15"]
