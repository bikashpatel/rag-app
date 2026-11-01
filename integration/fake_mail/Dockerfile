# The integration stack's fake SMTP server (server.py: SMTP on 2525, HTTP on 8025), standard library only. The base is
# the backend's, pinned by the same digest.
FROM python:3.13.15-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY server.py /fake_mail/server.py
RUN useradd --create-home --uid 10001 fakemail
USER fakemail

EXPOSE 2525 8025
HEALTHCHECK --interval=3s --timeout=5s --retries=20 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8025/health', timeout=3)"]
CMD ["python", "/fake_mail/server.py", "--host", "0.0.0.0", "--smtp-port", "2525", "--http-port", "8025"]
