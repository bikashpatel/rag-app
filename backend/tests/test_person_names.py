"""A person's name (SPEC.md section 4, users.display_name): the shared rules (app.schemas_auth.person_name), the name
in Me and the account, changing it (PUT /me/name, its per-user limit), a Google sign-up's name, the admin's create and
update, and the export. Sign-up's name is in test_signup.py, receipts' in test_billing_receipts.py, deletion in
test_deletion_completeness.py, the migration in test_migration_0006.py."""

import io
import json
import zipfile
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from billing_support import events
from conftest import GOOGLE_FINISH, ApiClient, OutsideHosts, open_signup
from fake_google import FakeGoogleSignin
from google_oidc import GoogleOidc
from pydantic import ValidationError
from signup_support import google_callback, google_fake, user_row

from app.schemas_auth import NAME_MAX_LENGTH, NameChange
from app.services.auth import throttle

CreateUser = Callable[..., Awaitable[ApiClient]]


# ---------------- The rules ----------------


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("Bikash Patel", "Bikash Patel"),
        ("  Bikash \t\n Patel  ", "Bikash Patel"),
        ("Ana\N{NO-BREAK SPACE}Mar\N{IDEOGRAPHIC SPACE}Ruiz", "Ana Mar Ruiz"),
        ("Rene\N{COMBINING ACUTE ACCENT}e Côté", "Renée Côté"),  # NFC
        ("बिकाश पटेल", "बिकाश पटेल"),
        ("ശ്\N{ZERO WIDTH JOINER}രീ", "ശ്\N{ZERO WIDTH JOINER}രീ"),  # a joiner some scripts need
        ("Dr. A. P. J. Abdul Kalam", "Dr. A. P. J. Abdul Kalam"),
        ("O'Brien-Smith 🌻", "O'Brien-Smith 🌻"),
        ("é" * NAME_MAX_LENGTH, "é" * NAME_MAX_LENGTH),  # characters, not bytes
    ],
)
def test_a_name_is_normalized(typed: str, stored: str) -> None:
    assert NameChange.model_validate({"name": typed}).name == stored


@pytest.mark.parametrize(
    ("typed", "reason"),
    [
        ("", "Enter your name."),
        (" \t\N{NO-BREAK SPACE} ", "Enter your name."),
        ("é" * (NAME_MAX_LENGTH + 1), "Use at most 80 characters."),
        ("<b>Bikash</b>", "Leave out < and >."),
        ("Bikash >", "Leave out < and >."),
        ("Bikash\x00Patel", "Remove the invisible or control characters."),
        ("Bikash\x1fPatel", "Remove the invisible or control characters."),
        ("Bikash\x85Patel", "Remove the invisible or control characters."),
        ("Bikash\N{RIGHT-TO-LEFT OVERRIDE}letaP", "Remove the invisible or control characters."),
        ("\N{ZERO WIDTH NO-BREAK SPACE}Bikash", "Remove the invisible or control characters."),
        ("Bikash\N{ZERO WIDTH SPACE}", "Remove the invisible or control characters."),
    ],
)
def test_a_name_is_refused_with_a_reason(typed: str, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        NameChange.model_validate({"name": typed})


@pytest.mark.parametrize("body", [{}, {"name": None}, {"name": 7}, {"name": "Bikash", "nickname": "B"}])
def test_a_name_change_needs_one_string(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NameChange.model_validate(body)


# ---------------- The caller's name ----------------


async def test_an_account_starts_without_a_name_and_the_caller_sets_it(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    assert (await alice.get("/api/auth/me")).json()["display_name"] is None
    assert (await alice.get("/api/me/account")).json()["display_name"] is None

    response = await alice.put("/api/me/name", json={"name": "  Alice   Liddell "})

    assert response.status_code == 200 and response.json() == {"display_name": "Alice Liddell"}
    assert (await alice.get("/api/auth/me")).json()["display_name"] == "Alice Liddell"
    assert (await alice.get("/api/me/account")).json()["display_name"] == "Alice Liddell"
    assert (await user_row(alice.user_id)).display_name == "Alice Liddell"


async def test_a_refused_name_changes_nothing(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    assert (await alice.put("/api/me/name", json={"name": "Alice"})).status_code == 200

    for body in ({"name": "  "}, {"name": "<Alice>"}, {"name": "a" * 81}, {}):
        assert (await alice.put("/api/me/name", json=body)).status_code == 422, body

    assert (await user_row(alice.user_id)).display_name == "Alice"


async def test_the_name_needs_a_session_and_the_env_admin_may_set_one(
    client: ApiClient, admin_client: ApiClient
) -> None:
    assert (await client.put("/api/me/name", json={"name": "Nobody"})).status_code == 401

    response = await admin_client.put("/api/me/name", json={"name": "Bikash Patel"})

    assert response.status_code == 200
    assert (await admin_client.get("/api/auth/me")).json()["display_name"] == "Bikash Patel"


async def test_ten_changes_an_hour_per_user(create_user: CreateUser) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    for number in range(10):
        assert (await alice.put("/api/me/name", json={"name": f"Alice {number}"})).status_code == 200

    refused = await alice.put("/api/me/name", json={"name": "Alice Again"})

    assert refused.status_code == 429 and refused.json()["code"] == "too_many_attempts"
    assert int(refused.headers["retry-after"]) > 0
    assert (await user_row(alice.user_id)).display_name == "Alice 9"
    assert (await alice.put("/api/me/name", json={"name": "<"})).status_code == 422  # validated before counting
    assert (await bob.put("/api/me/name", json={"name": "Bob"})).status_code == 200  # per user
    assert [(w.seconds, w.limit) for w in throttle.RULES["name_change"].windows] == [(3600, 10), (86_400, 30)]


# ---------------- Google sign-up ----------------


@pytest.fixture
async def google_signup(google_oidc: GoogleOidc, outside_hosts: OutsideHosts) -> FakeGoogleSignin:
    fake = google_fake(google_oidc, outside_hosts)
    await open_signup(email=False)
    return fake


async def test_a_google_sign_up_needs_a_name_and_keeps_its_ticket_until_it_has_one(
    client: ApiClient, google_signup: FakeGoogleSignin, mailer: None
) -> None:
    await google_callback(client, google_signup)

    for body in ({"accept_terms": True}, {"name": " ", "accept_terms": True}, {"name": "<x>", "accept_terms": True}):
        assert (await client.post("/api/auth/google/finish", json=body)).status_code == 422, body
    assert (await client.get("/api/auth/google/pending")).status_code == 200  # the ticket is still there

    finished = await client.post("/api/auth/google/finish", json={**GOOGLE_FINISH, "name": " Kavya   Iyer "})

    assert finished.status_code == 200, finished.text
    me = finished.json()
    assert me["display_name"] == "Kavya Iyer" and me["username"].startswith("user-")
    assert (await user_row(me["id"])).display_name == "Kavya Iyer"


# ---------------- The admin ----------------


async def test_the_admin_creates_a_user_with_or_without_a_name(admin_client: ApiClient) -> None:
    created = await admin_client.post(
        "/api/admin/users", json={"username": "bikash", "password": "a long passphrase", "display_name": " Bikash  P "}
    )
    blank = await admin_client.post(
        "/api/admin/users", json={"username": "nameless", "password": "a long passphrase", "display_name": "  "}
    )
    bad = await admin_client.post(
        "/api/admin/users", json={"username": "marked", "password": "a long passphrase", "display_name": "<b>"}
    )

    assert created.status_code == 201 and created.json()["display_name"] == "Bikash P"
    assert blank.status_code == 201 and blank.json()["display_name"] is None
    assert bad.status_code == 422
    listed = {user["username"]: user["display_name"] for user in (await admin_client.get("/api/admin/users")).json()}
    assert (listed["bikash"], listed["nameless"]) == ("Bikash P", None) and "marked" not in listed


async def test_the_admin_sets_keeps_and_clears_a_name(admin_client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice")
    path = f"/api/admin/users/{alice.user_id}"

    async def name_after(body: dict[str, object]) -> object:
        response = await admin_client.patch(path, json=body)
        assert response.status_code == 200, response.text
        return response.json()["display_name"]

    assert await name_after({"display_name": "Alice  Liddell"}) == "Alice Liddell"
    assert await name_after({"billing_tester": True}) == "Alice Liddell"  # omitted: unchanged
    assert (await admin_client.patch(path, json={"display_name": "a" * 81})).status_code == 422
    assert await name_after({"display_name": ""}) is None
    assert await name_after({"display_name": "Alice"}) == "Alice"
    assert await name_after({"display_name": None}) is None
    assert (await user_row(alice.user_id)).display_name is None
    # Billing events outlive the user: no name in them
    assert all("Liddell" not in str(event.detail) for event in await events())


# ---------------- The export ----------------


async def exported_account(client: ApiClient) -> Any:
    response = await client.post("/api/me/export", json={})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return json.loads(archive.read("account.json"))


async def test_the_export_holds_the_name(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    assert (await exported_account(alice))["display_name"] is None
    assert (await alice.put("/api/me/name", json={"name": "Alice Liddell"})).status_code == 200

    profile = await exported_account(alice)

    assert (profile["username"], profile["display_name"]) == ("alice", "Alice Liddell")
