"""A fake alert receiver for the monitor in the integration stack: a webhook (POST /webhook, JSON {"text": ...}) and an
ntfy topic (POST /ntfy/<topic>, the text as the body, Title/Priority/Tags headers). Never a real service.

    python /fake_alerts/server.py --host 0.0.0.0 --port 8000

GET /_log: what was received, oldest first. POST /_fail {"count": N, "status": 500}: the next N alert requests fail.
GET /health: 200.
"""

import argparse
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response


def build_app() -> FastAPI:
    app = FastAPI()
    received: list[dict[str, Any]] = []
    failing = {"count": 0, "status": 500}

    def record(channel: str, request: Request, text: str) -> Response:
        if failing["count"] > 0:
            failing["count"] -= 1
            return Response(status_code=failing["status"])
        headers = {name: request.headers.get(name, "") for name in ("title", "priority", "tags", "content-type")}
        received.append({"channel": channel, "path": request.url.path, "text": text, "headers": headers})
        return Response(status_code=200)

    @app.post("/webhook")
    async def webhook(request: Request) -> Response:
        body = await request.json()
        return record("webhook", request, str(body.get("text", "")))

    @app.post("/ntfy/{topic}")
    async def ntfy(topic: str, request: Request) -> Response:
        return record("ntfy", request, (await request.body()).decode())

    @app.get("/_log")
    async def log() -> dict[str, Any]:
        return {"received": received}

    @app.post("/_fail")
    async def fail(request: Request) -> dict[str, int]:
        failing.update(await request.json())
        return failing

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    uvicorn.run(build_app(), host=arguments.host, port=arguments.port, log_level="warning")


if __name__ == "__main__":
    main()
