"""Flow 13: the vision and image models, both pointed at the fake.

- A scanned PDF (an image only, no text layer) fails with the "enable a vision model" error while no vision model is
  active; after the admin activates the fake vision model, Retry sends the rendered page to it (an image_url part),
  stores its transcript and makes the document ready and searchable, with vision usage recorded.
- Asking for an image makes the fake call create_image; the backend calls the fake image model, validates and stores
  the PNG as the user's private generated image, and serves it with its renditions."""

import io

import pytest
from harness import assert_sse_contract, chat, check, document, unique, upload, wait_document
from PIL import Image
from samples import scanned_pdf

FAKE_TRANSCRIPT_WORDS = ("cobalt", "lighthouse", "lamps")  # in the fake's default vision_reply


@pytest.mark.deadline(300)
def test_scanned_pdf_needs_the_vision_model_and_retry_reads_it(make_user, admin, models: dict, fake) -> None:
    vision = models["vision"]["id"]
    user = make_user("vision")
    check(admin.patch(f"/api/admin/models/{vision}", json={"enabled": False}))
    try:
        assert check(admin.get("/api/admin/stats")).json()["active_vision_model"] is None
        item = upload(
            user.client, f"{unique('scan')}.pdf", scanned_pdf("COBALT LIGHTHOUSE INVENTORY\n4712 brass lamps")
        )
        failed = wait_document(user.client, item["id"], status="failed")
        assert "Ask the admin to enable a vision model to read it." in failed["error"]
    finally:
        check(admin.patch(f"/api/admin/models/{vision}", json={"enabled": True, "is_default": True}))
    assert check(admin.get("/api/admin/stats")).json()["active_vision_model"]["id"] == vision

    mark = fake.mark()
    check(user.client.post(f"/api/documents/{item['id']}/reprocess"))
    ready = wait_document(user.client, item["id"], timeout=180)
    assert ready["vision_pages"] == 1 and ready["num_pages"] == 1 and ready["num_chunks"] >= 1
    (call,) = fake.log("vision", after=mark)
    parts = call["messages"][-1]["content"]
    assert any(
        part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/jpeg;base64,") for part in parts
    )
    assert call["auth"] == "placeholder"

    result = chat(
        user.client, "How many brass lamps does the cobalt lighthouse inventory list?", document_ids=[item["id"]]
    )
    assert result.sources and result.sources[0]["document_id"] == item["id"] and result.sources[0]["page"] == 1
    assert all(word in result.sources[0]["snippet"].lower() for word in FAKE_TRANSCRIPT_WORDS)
    usage = check(user.client.get("/api/me/usage")).json()
    assert usage["vision_tokens"]["used"] == call["usage"]["prompt_tokens"] + call["usage"]["completion_tokens"]


@pytest.mark.deadline(240)
def test_image_from_chat_is_generated_stored_and_served(make_user, fake, admin) -> None:
    user = make_user("images")
    title = f"Red lighthouse {unique('')}"
    mark = fake.mark()
    result = chat(user.client, f'Please generate an image of "{title}" at sunset.')
    assert_sse_contract(result)
    assert result.names[-1] == "done", result.events[-1]
    events = result.all("file")
    statuses = [event["status"] for event in events]
    assert statuses[0] == "writing" and "generating" in statuses and statuses[-1] == "created", statuses
    assert all(event["call_id"] == "i1" and event["kind"] == "image" for event in events)
    created = events[-1]
    assert created["file"]["format"] == "png" and created["file"]["image"]["width"] == 1024
    doc = created["document"]
    assert doc["generated"] is True and doc["image"] is not None and doc["owner_id"] == user.id

    (generation,) = fake.log("image", after=mark)
    assert generation["size"] == "1024x1024" and generation["quality"] == "low" and title in generation["prompt"]
    assert generation["auth"] == "placeholder"

    full = check(user.client.get(f"/api/documents/{doc['id']}/image"))
    assert full.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(full.content)) as image:
        assert image.format == "PNG" and image.size == (1024, 1024)
    thumb = check(user.client.get(f"/api/documents/{doc['id']}/image", params={"size": "thumb"}))
    assert thumb.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumb.content)) as image:
        assert max(image.size) == 256
    details = check(user.client.get(f"/api/documents/{doc['id']}/image/details")).json()
    assert title in details["prompt"] and details["model_name"] == "openai/gpt-image-1"

    ready = wait_document(user.client, doc["id"])
    assert ready["vision_pages"] == 0 and ready["num_chunks"] >= 1
    other = make_user("images-other")
    assert other.client.get(f"/api/documents/{doc['id']}/image").status_code == 404
    assert document(other.client, doc["id"]) is None

    usage = check(user.client.get("/api/me/usage")).json()
    assert usage["images"]["used"] == 1
    report = check(admin.get("/api/admin/usage")).json()
    assert next(row for row in report["by_user"] if row["user_id"] == user.id)["images"] == 1


def test_monthly_image_limit_stops_the_tool(make_user, fake) -> None:
    user = make_user("images-limit", monthly_image_limit=1)
    first = chat(user.client, f'Please generate an image of "{unique("Fox")}"')
    assert first.all("file")[-1]["status"] == "created"
    mark = fake.mark()
    second = chat(user.client, f'Please generate an image of "{unique("Owl")}"')
    assert second.names[-1] == "done"
    assert not [event for event in second.all("file") if event["status"] == "created"]
    assert fake.log("image", after=mark) == []
    assert "create_image" not in fake.log("chat", after=mark)[0]["tools"]
