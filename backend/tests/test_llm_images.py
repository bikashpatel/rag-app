"""llm.generate_image against a local fake OpenAI images endpoint, with the real LiteLLM
(docs/briefs/image-generation.md, sections 1 and 18.4): the request body it sends, the reply it parses, refusals, and
how many requests slow, stopped and failing calls make. These pin LiteLLM 1.103.1's behaviour, so an upgrade that
changes it fails here."""

import asyncio
import base64
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import litellm
import pytest
import uvicorn
from conftest import fake_image
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.models import LLMModel
from app.services import llm

pytestmark = pytest.mark.filterwarnings("ignore:Accessing the 'model_")

_REAL_AIMAGE_GENERATION = litellm.aimage_generation  # the autouse fake_llm fixture replaces it in every test
KEY_ENV = "IMAGES_TEST_API_KEY"
PNG = fake_image(64, 48)
MODERATION = {
    "error": {
        "message": "Your request was rejected by the safety system. If you believe this is an error, contact us at "
        "help.openai.com and include the request ID req_123.",
        "type": "image_generation_user_error",
        "param": None,
        "code": "moderation_blocked",
    }
}
POLICY = {
    "error": {
        "code": "content_policy_violation",
        "message": "Your request was rejected as a result of our safety system.",
        "param": None,
        "type": "invalid_request_error",
    }
}


@dataclass
class ImagesServer:
    """A local /v1/images/generations endpoint. `mode`: "ok", "moderation", "policy", "500" or "slow" (answers after
    `delay` seconds, unless the client goes away first: then `disconnected` records it)."""

    mode: str = "ok"
    delay: float = 3.0
    requests: list[dict[str, Any]] = field(default_factory=list)
    disconnected: list[bool] = field(default_factory=list)

    def __post_init__(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        app = Starlette(routes=[Route("/v1/images/generations", self.generations, methods=["POST"])])
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    async def generations(self, request: Request) -> JSONResponse:
        body = await request.json()
        self.requests.append({"body": body, "authorization": request.headers.get("authorization", "")})
        if self.mode == "moderation":
            return JSONResponse(MODERATION, status_code=400)
        if self.mode == "policy":
            return JSONResponse(POLICY, status_code=400)
        if self.mode == "500":
            return JSONResponse({"error": {"message": "server exploded", "type": "server_error"}}, status_code=500)
        if self.mode == "slow":
            deadline = time.monotonic() + self.delay
            while time.monotonic() < deadline:
                if await request.is_disconnected():
                    self.disconnected.append(True)
                    return JSONResponse({}, status_code=499)
                await asyncio.sleep(0.02)
        return JSONResponse(
            {
                "created": 1_760_000_000,
                "background": "opaque",
                "output_format": body.get("output_format", "png"),
                "quality": body.get("quality"),
                "size": body.get("size"),
                "data": [{"b64_json": base64.b64encode(PNG).decode(), "revised_prompt": "A fox, revised"}],
                "usage": {
                    "input_tokens": 50,
                    "input_tokens_details": {"image_tokens": 0, "text_tokens": 50},
                    "output_tokens": 272,
                    "output_tokens_details": {"image_tokens": 272, "text_tokens": 0},
                    "total_tokens": 322,
                },
            }
        )


@pytest.fixture(scope="module")
def images_server() -> Iterator[ImagesServer]:
    server = ImagesServer()
    server.thread.start()
    deadline = time.monotonic() + 10
    while not server.server.started:
        assert time.monotonic() < deadline, "the images server didn't start"
        time.sleep(0.02)
    yield server
    server.server.should_exit = True
    server.thread.join(timeout=5)


@pytest.fixture
def server(images_server: ImagesServer, monkeypatch: pytest.MonkeyPatch) -> ImagesServer:
    monkeypatch.setattr(litellm, "aimage_generation", _REAL_AIMAGE_GENERATION)
    monkeypatch.setenv(KEY_ENV, "test-images-key")
    images_server.mode, images_server.delay = "ok", 3.0
    images_server.requests.clear()
    images_server.disconnected.clear()
    return images_server


def model(server: ImagesServer) -> LLMModel:
    return LLMModel(
        id=1,
        display_name="GPT Image 2",
        litellm_model="openai/gpt-image-2",
        kind="image",
        api_base=f"http://127.0.0.1:{server.port}/v1",
        api_key_env=KEY_ENV,
    )


async def generate(server: ImagesServer, **options: Any) -> llm.ImageReply:
    settings = {"size": "1024x1024", "quality": "low", "output_format": "png", "timeout": 20.0} | options
    return await llm.generate_image(model(server), "A fox", **settings)


async def test_the_request_body_and_the_reply(server: ImagesServer) -> None:
    reply = await generate(server)

    [request] = server.requests
    assert request["body"] == {
        "model": "gpt-image-2",
        "prompt": "A fox",
        "n": 1,
        "size": "1024x1024",
        "quality": "low",
        "output_format": "png",
    }
    assert request["authorization"] == "Bearer test-images-key"
    assert reply.data == PNG and reply.revised_prompt == "A fox, revised"
    assert (reply.usage.prompt_tokens, reply.usage.completion_tokens, reply.usage.estimated) == (50, 272, False)
    assert reply.usage.cost_usd == pytest.approx(0.00841)  # LiteLLM's prices for gpt-image-2


async def test_webp_asks_for_compression(server: ImagesServer) -> None:
    await generate(server, size="1536x1024", quality="medium", output_format="webp")
    [request] = server.requests
    assert request["body"] == {
        "model": "gpt-image-2",
        "prompt": "A fox",
        "n": 1,
        "size": "1536x1024",
        "quality": "medium",
        "output_format": "webp",
        "output_compression": 90,
    }


@pytest.mark.parametrize("mode", ["moderation", "policy"])
async def test_refusals_after_one_request(server: ImagesServer, mode: str) -> None:
    server.mode = mode
    with pytest.raises(llm.ImageCallError) as caught:
        await generate(server)
    assert caught.value.refused and not caught.value.billed
    assert len(server.requests) == 1


async def test_a_slow_response_is_abandoned_after_one_request(server: ImagesServer) -> None:
    server.mode, server.delay = "slow", 3.0
    started = time.monotonic()
    with pytest.raises(llm.ImageCallError) as caught:
        await generate(server, timeout=1.0)
    assert time.monotonic() - started < 2.5
    assert caught.value.billed and caught.value.usage is None
    await asyncio.sleep(1.0)  # an SDK retry would arrive by now
    assert len(server.requests) == 1


async def test_stop_closes_the_connection_after_one_request(server: ImagesServer) -> None:
    server.mode, server.delay = "slow", 5.0
    call = asyncio.create_task(generate(server, timeout=60.0))
    deadline = time.monotonic() + 10
    while not server.requests:
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)

    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    while not server.disconnected:
        assert time.monotonic() < deadline, "the server never saw the connection close"
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.5)
    assert len(server.requests) == 1


async def test_a_500_is_retried_by_the_sdk(server: ImagesServer) -> None:
    """Documents I5: LiteLLM's image path ignores max_retries, so the OpenAI SDK retries 5xx twice. If an upgrade makes
    this one request, update the brief's I5."""
    server.mode = "500"
    with pytest.raises(llm.ImageCallError) as caught:
        await generate(server)
    assert not (caught.value.billed or caught.value.refused or caught.value.unusable)
    assert str(caught.value).startswith("Image generation failed: ")
    assert len(server.requests) == 3
