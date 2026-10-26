"""Guards on every route, enumerated from the app's route table (the routes hidden from the OpenAPI schema included) so
that a new route is checked without being listed: an anonymous caller gets 401 everywhere except the public routes, and
every route that takes the id of a user's resource answers another user exactly as it answers a missing id (404). The
admin routes' 403 is in test_admin.py."""

from collections.abc import Awaitable, Callable

import httpx
import pytest
from conftest import ApiClient, chat, fake_image, ingest, txt_bytes
from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Conversation, Document, GeneratedImage, Message
from app.services import storage

CreateUser = Callable[..., Awaitable[ApiClient]]
Operation = tuple[str, str]  # (method, path template)

PUBLIC: set[Operation] = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/health"),
    ("GET", "/api/health/ready"),
    # Identified by the server-side state and the nonce cookie: the browser arrives from the provider's site, without
    # the SameSite=Strict session cookie.
    ("GET", "/api/plugins/{plugin_id}/oauth/callback"),
}

# Routes whose path holds the id of a user's resource, checked against another user's below.
OWNER_CHECKED: set[Operation] = {
    ("DELETE", "/api/documents/{doc_id}"),
    ("POST", "/api/documents/{doc_id}/reprocess"),
    ("GET", "/api/documents/{doc_id}/download"),
    ("GET", "/api/documents/{doc_id}/image"),
    ("GET", "/api/documents/{doc_id}/image/details"),
    ("GET", "/api/conversations/{conversation_id}"),
    ("PATCH", "/api/conversations/{conversation_id}"),
    ("DELETE", "/api/conversations/{conversation_id}"),
}
# Checked with a connected Gmail account in test_gmail_sends.py::test_other_users_get_404.
OWNER_CHECKED_ELSEWHERE: set[Operation] = {
    ("GET", "/api/plugins/gmail/sends/{send_id}"),
    ("POST", "/api/plugins/gmail/sends/{send_id}/send"),
    ("POST", "/api/plugins/gmail/sends/{send_id}/cancel"),
    ("POST", "/api/plugins/gmail/sends/{send_id}/revise"),
}
# Path parameters that aren't a user's resource: a plugin id, or a thread of the caller's own connected mailbox.
NOT_A_USERS_RESOURCE: set[Operation] = {
    ("POST", "/api/plugins/{plugin_id}/connect"),
    ("GET", "/api/plugins/{plugin_id}/oauth/callback"),
    ("PATCH", "/api/plugins/{plugin_id}/connection"),
    ("DELETE", "/api/plugins/{plugin_id}/connection"),
    ("GET", "/api/plugins/gmail/threads/{thread_id}"),
}

DOCUMENT_NOT_FOUND = {"detail": "Document not found"}
CONVERSATION_NOT_FOUND = {"detail": "Conversation not found"}


def api_routes(app: FastAPI) -> dict[Operation, RouteContext]:
    """Every (method, path) the app serves through an API route, with the route as included (its full path), whether
    or not the OpenAPI schema shows it. FastAPI's own docs routes aren't API routes."""
    return {
        (method, route.path): route
        for route in iter_route_contexts(app.routes)
        if isinstance(route.original_route, APIRoute)
        for method in route.methods
    }


def concrete(route: RouteContext) -> str:
    """The route's path with a placeholder for each path parameter: 1 for an integer, else "gmail"."""
    path = route.path
    for parameter in route.dependant.path_params:
        placeholder = "1" if parameter.field_info.annotation is int else "gmail"
        path = path.replace("{" + parameter.name + "}", placeholder)
    return path


async def send(client: httpx.AsyncClient, method: str, url: str) -> httpx.Response:
    body = {"json": {}} if method in ("POST", "PATCH", "PUT") else {}
    return await client.request(method, url, **body)


def test_the_route_table_holds_every_route_of_the_api_schema(app: FastAPI) -> None:
    schema = {(method.upper(), path) for path, item in app.openapi()["paths"].items() for method in item}
    assert schema <= set(api_routes(app)), schema - set(api_routes(app))


def test_the_route_lists_name_real_routes(app: FastAPI) -> None:
    served = set(api_routes(app))
    for listed in (PUBLIC, OWNER_CHECKED, OWNER_CHECKED_ELSEWHERE, NOT_A_USERS_RESOURCE):
        assert listed <= served, listed - served


def test_every_route_with_a_path_id_outside_admin_is_classified(app: FastAPI) -> None:
    """A new route taking an id must be added to OWNER_CHECKED (and so be checked below) or explained."""
    with_ids = {
        key
        for key, route in api_routes(app).items()
        if route.dependant.path_params and not key[1].startswith("/api/admin/")
    }
    assert with_ids == OWNER_CHECKED | OWNER_CHECKED_ELSEWHERE | NOT_A_USERS_RESOURCE


async def test_anonymous_callers_get_401_everywhere_but_the_public_routes(app: FastAPI, client: ApiClient) -> None:
    checked = 0
    for (method, path), route in sorted(api_routes(app).items(), key=lambda item: item[0]):
        if (method, path) in PUBLIC:
            continue
        response = await send(client, method, concrete(route))
        assert response.status_code == 401, (method, path, response.status_code, response.text)
        assert response.json() == {"detail": "Not authenticated"}, (method, path)
        assert "set-cookie" not in response.headers, (method, path)
        checked += 1
    assert checked >= 50


async def image_document(owner_id: int) -> int:
    """A generated image owned by `owner_id`, written to storage as the image tool would."""
    stored_name = storage.new_stored_name(".png")
    path = storage.stored_path(stored_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = fake_image()
    path.write_bytes(data)
    async with SessionLocal() as db:
        document = Document(
            owner_id=owner_id,
            scope="private",
            filename="fox.png",
            stored_name=stored_name,
            content_type="image/png",
            size_bytes=len(data),
            sha256="a" * 64,
            status="ready",
        )
        db.add(document)
        await db.flush()
        db.add(
            GeneratedImage(
                document_id=document.id,
                model_name="openai/gpt-image-2",
                prompt="A fox",
                alt_text="A fox",
                format="png",
                quality="low",
                width=64,
                height=64,
            )
        )
        await db.commit()
        return document.id


async def test_another_users_resources_answer_like_missing_ones_on_every_owner_checked_route(
    create_user: CreateUser,
) -> None:
    alice = await create_user("alice")
    bob = await create_user("bob")
    document = await ingest(alice, "diary.txt", txt_bytes("Private diary entry about the spring garden."))
    image_id = await image_document(alice.user_id)  # type: ignore[arg-type]
    _, events = await chat(alice, "What grows in the spring garden?")
    conversation_id = events[0][1]["conversation_id"]
    missing = 10_000

    requests: dict[Operation, Callable[[ApiClient, int, int, int], Awaitable[httpx.Response]]] = {
        ("DELETE", "/api/documents/{doc_id}"): lambda c, d, i, v: c.delete(f"/api/documents/{d}"),
        ("POST", "/api/documents/{doc_id}/reprocess"): lambda c, d, i, v: c.post(f"/api/documents/{d}/reprocess"),
        ("GET", "/api/documents/{doc_id}/download"): lambda c, d, i, v: c.get(f"/api/documents/{d}/download"),
        ("GET", "/api/documents/{doc_id}/image"): lambda c, d, i, v: c.get(f"/api/documents/{i}/image"),
        ("GET", "/api/documents/{doc_id}/image/details"): lambda c, d, i, v: c.get(
            f"/api/documents/{i}/image/details"
        ),
        ("GET", "/api/conversations/{conversation_id}"): lambda c, d, i, v: c.get(f"/api/conversations/{v}"),
        ("PATCH", "/api/conversations/{conversation_id}"): lambda c, d, i, v: c.patch(
            f"/api/conversations/{v}", json={"title": "Taken over"}
        ),
        ("DELETE", "/api/conversations/{conversation_id}"): lambda c, d, i, v: c.delete(f"/api/conversations/{v}"),
    }
    assert set(requests) == OWNER_CHECKED

    for key, request in requests.items():
        theirs = await request(bob, document["id"], image_id, conversation_id)
        absent = await request(bob, missing, missing, missing)
        assert theirs.status_code == absent.status_code == 404, (key, theirs.status_code, absent.status_code)
        assert theirs.json() == absent.json(), key
        expected = CONVERSATION_NOT_FOUND if "conversation" in key[1] else DOCUMENT_NOT_FOUND
        assert theirs.json() == expected, key

    # Chat names the conversation in its body.
    response, _ = await chat(bob, "Continue this", conversation_id=conversation_id)
    assert (response.status_code, response.json()) == (404, CONVERSATION_NOT_FOUND)

    # Nothing of Alice's changed, and she still reaches all of it.
    async with SessionLocal() as db:
        row = await db.get(Document, document["id"])
        assert row is not None and row.status == "ready"
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None and conversation.title != "Taken over"
        messages = await db.scalars(select(Message.role).where(Message.conversation_id == conversation_id))
        assert list(messages) == ["user", "assistant"]
    for key, request in requests.items():
        if key[0] == "DELETE":
            continue
        response = await request(alice, document["id"], image_id, conversation_id)
        assert response.status_code == 200, (key, response.status_code, response.text)
    assert storage.stored_path(row.stored_name).is_file()


@pytest.mark.parametrize("route", ["download", "image", "image/details"])
async def test_shared_documents_are_readable_but_not_manageable_by_users(
    route: str, create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    shared = await ingest(admin_client, "policy.txt", txt_bytes("Company travel policy for all employees."), "shared")
    readable = await alice.get(f"/api/documents/{shared['id']}/{route}")
    if route == "download":
        assert readable.status_code == 200 and readable.content.startswith(b"Company travel policy")
    else:  # visible, but not an image
        assert (readable.status_code, readable.json()) == (404, {"detail": "This document isn't an image."})
    for response in (
        await alice.delete(f"/api/documents/{shared['id']}"),
        await alice.post(f"/api/documents/{shared['id']}/reprocess"),
    ):
        assert (response.status_code, response.json()) == (404, DOCUMENT_NOT_FOUND)
    assert any(item["id"] == shared["id"] for item in (await alice.get("/api/documents")).json())
