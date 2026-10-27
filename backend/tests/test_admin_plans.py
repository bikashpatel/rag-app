"""The admin's plans (docs/briefs/plans-billing.md 3.1, 3.5 and 8.10; routers/admin_billing.py, services/billing/
plans.py) and the public plan list: listing with counts and cost estimates, creating and validating, editing with and
without applying to current periods (and the reduction confirmation), deleting only unused plans, the audit of every
change."""

from typing import Any

import pytest
from billing_support import CreateUser, buy, events, new_tester, periods_of
from conftest import ApiClient
from fake_razorpay import FakeRazorpay

from app import db
from app.models import User
from app.services.billing import entitlements

LIMITS = {
    "monthly_token_limit": 300_000,
    "rate_limit_per_min": 5,
    "max_upload_mb": 5,
    "max_documents": 20,
    "max_storage_mb": 100,
    "monthly_image_limit": 0,
    "vision_monthly_token_limit": 100_000,
}


def new_plan(**changes: Any) -> dict[str, Any]:
    return {
        "code": "lite",
        "name": "Lite",
        "description": "Just the basics.",
        "price_paise": 20_000,
        "rank": 5,
        "status": "active",
        "recommended": False,
        "features": {"files": False, "images": False, "plugins": []},
        "limits": LIMITS,
        "chat_model_ids": None,
        "default_chat_model_id": None,
        **changes,
    }


async def plans(admin: ApiClient) -> list[dict[str, Any]]:
    response = await admin.get("/api/admin/plans")
    assert response.status_code == 200, response.text
    return response.json()


async def by_code(admin: ApiClient, code: str) -> dict[str, Any]:
    return next(plan for plan in await plans(admin) if plan["code"] == code)


async def test_the_seeded_plans_with_counts_and_cost_estimates(
    admin_client: ApiClient, create_user: CreateUser
) -> None:
    await create_user("alice")  # an open-ended Professional plan

    found = await plans(admin_client)

    assert [plan["code"] for plan in found] == ["starter", "standard", "professional"]
    starter, standard, professional = found
    assert starter["chat_model_ids"] is not None and professional["chat_model_ids"] is None
    assert standard["recommended"] is True and starter["used"] is False
    assert professional["complimentary_users"] == 1 and professional["used"] is True
    assert starter["cost"] == {
        "kept_paise": 48_820,
        "worst_case_paise": 17_169,
        "worst_share": pytest.approx(0.3517, abs=1e-4),
        "typical_paise": 6_868,
        "typical_share": pytest.approx(0.1407, abs=1e-4),
        "unlimited": [],
    }
    assert professional["cost"]["worst_case_paise"] == 183_568
    assert professional["cost"]["worst_share"] == pytest.approx(0.376, abs=1e-3)


async def test_creating_a_plan(admin_client: ApiClient) -> None:
    sonnet = next(
        model["id"]
        for model in (await admin_client.get("/api/admin/models")).json()
        if model["display_name"] == "Claude Sonnet 5.5"
    )

    response = await admin_client.post(
        "/api/admin/plans",
        json=new_plan(recommended=True, chat_model_ids=[sonnet], default_chat_model_id=sonnet),
    )

    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["applied_periods"] == 0 and saved["plan"]["code"] == "lite"
    assert saved["plan"]["chat_model_ids"] == [sonnet] and saved["plan"]["cost"]["unlimited"] == []
    assert [plan["code"] for plan in await plans(admin_client) if plan["recommended"]] == ["lite"]
    (created,) = await events("plan.created")
    assert created.actor == "admin" and created.detail["code"] == "lite"
    public = (await admin_client.get("/api/public/plans")).json()
    assert public[0]["code"] == "lite" and public[0]["default_chat_model"]["display_name"] == "Claude Sonnet 5.5"


@pytest.mark.parametrize(
    ("changes", "status", "detail"),
    [
        ({"code": "starter"}, 409, "A plan with the code starter exists."),
        ({"rank": 10}, 409, "Another plan has rank 10."),
        ({"rank": 15}, 422, "A higher plan must cost more."),
        ({"features": {"files": False, "images": False, "plugins": ["slack"]}}, 422, "Unknown plugin: slack."),
        ({"chat_model_ids": [9999]}, 422, "Unknown chat model: 9999."),
    ],
)
async def test_plan_rules(admin_client: ApiClient, changes: dict[str, Any], status: int, detail: str) -> None:
    response = await admin_client.post("/api/admin/plans", json=new_plan(**changes))

    assert response.status_code == status and response.json()["detail"] == detail


@pytest.mark.parametrize(
    "changes",
    [
        {"price_paise": 20_050},
        {"status": "retired", "recommended": True},
        {"code": "Has Spaces"},
        {"limits": {**LIMITS, "max_upload_mb": 0}},
        {"extra": True},
    ],
)
async def test_invalid_plans_are_422(admin_client: ApiClient, changes: dict[str, Any]) -> None:
    assert (await admin_client.post("/api/admin/plans", json=new_plan(**changes))).status_code == 422


async def test_a_retired_plan_may_cost_less_than_a_lower_one(admin_client: ApiClient) -> None:
    response = await admin_client.post("/api/admin/plans", json=new_plan(rank=15, status="retired"))

    assert response.status_code == 201
    assert "lite" not in [plan["code"] for plan in (await admin_client.get("/api/public/plans")).json()]


async def test_editing_a_plan_applies_to_new_periods_and_is_audited(admin_client: ApiClient) -> None:
    starter = await by_code(admin_client, "starter")

    response = await admin_client.patch(
        f"/api/admin/plans/{starter['id']}", json={"price_paise": 60_000, "description": "Cheaper models."}
    )
    nulled = await admin_client.patch(f"/api/admin/plans/{starter['id']}", json={"name": None})
    unknown = await admin_client.patch("/api/admin/plans/999", json={"price_paise": 60_000})
    reordered = await admin_client.patch(f"/api/admin/plans/{starter['id']}", json={"price_paise": 300_000})

    assert response.status_code == 200 and response.json()["plan"]["price_paise"] == 60_000
    assert nulled.status_code == 422 and unknown.status_code == 404
    assert reordered.status_code == 422 and reordered.json()["detail"] == "A higher plan must cost more."
    (updated,) = await events("plan.updated")
    assert updated.detail["changed"] == {
        "price_paise": {"before": 50_000, "after": 60_000},
        "description": {
            "before": "Ask questions across your documents, for everyday use.",
            "after": "Cheaper models.",
        },
    }


async def test_unrestricting_models_and_a_default_outside_the_list(admin_client: ApiClient) -> None:
    starter = await by_code(admin_client, "starter")
    opus = next(
        model["id"]
        for model in (await admin_client.get("/api/admin/models")).json()
        if model["display_name"] == "Claude Opus 5.5"
    )

    outside = await admin_client.patch(f"/api/admin/plans/{starter['id']}", json={"default_chat_model_id": opus})
    everything = await admin_client.patch(f"/api/admin/plans/{starter['id']}", json={"chat_model_ids": None})

    assert outside.status_code == 422
    assert outside.json()["detail"] == "The default chat model must be one of the plan's models."
    assert everything.status_code == 200 and everything.json()["plan"]["chat_model_ids"] is None


@pytest.mark.usefixtures("fake_razorpay")
async def test_lowering_paid_periods_needs_a_confirmation(
    admin_client: ApiClient, create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    bob = await new_tester(create_user, "bob")
    await buy(bob, fake_razorpay, "starter")
    starter = await by_code(admin_client, "starter")
    lower = {"limits": {**starter["limits"], "monthly_token_limit": 400_000}, "apply_to_current": True}

    refused = await admin_client.patch(f"/api/admin/plans/{starter['id']}", json=lower)
    untouched = (await periods_of(bob.user_id or 0))[0]
    kept = (await by_code(admin_client, "starter"))["limits"]["monthly_token_limit"]
    confirmed = await admin_client.patch(f"/api/admin/plans/{starter['id']}", json={**lower, "confirm_reduction": True})

    assert refused.status_code == 409
    assert refused.json() == {
        "detail": "This lowers what 1 user paid for until their periods end.",
        "code": "confirmation_required",
    }
    assert untouched.snapshot["limits"]["monthly_token_limit"] == 500_000
    assert kept == 500_000
    assert confirmed.status_code == 200 and confirmed.json()["applied_periods"] == 1
    (period,) = await periods_of(bob.user_id or 0)
    assert period.snapshot["limits"]["monthly_token_limit"] == 400_000
    assert period.history[-1]["kind"] == "plan_applied" and period.charge_basis_paise == 50_000
    (applied,) = await events("plan.applied_to_current")
    assert applied.detail["count"] == 1


@pytest.mark.usefixtures("fake_razorpay")
async def test_raising_limits_or_new_periods_only_needs_no_confirmation(
    admin_client: ApiClient, create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    cara = await new_tester(create_user, "cara")
    await buy(cara, fake_razorpay, "starter")
    starter = await by_code(admin_client, "starter")
    higher = {"limits": {**starter["limits"], "monthly_token_limit": 600_000}}

    only_new = await admin_client.patch(f"/api/admin/plans/{starter['id']}", json=higher)
    (period,) = await periods_of(cara.user_id or 0)
    assert only_new.json()["applied_periods"] == 0 and period.snapshot["limits"]["monthly_token_limit"] == 500_000
    applied = await admin_client.patch(f"/api/admin/plans/{starter['id']}", json={**higher, "apply_to_current": True})

    assert applied.status_code == 200 and applied.json()["applied_periods"] == 1


async def test_deleting_only_unused_plans(admin_client: ApiClient, create_user: CreateUser) -> None:
    await create_user("dina")
    created = (await admin_client.post("/api/admin/plans", json=new_plan())).json()["plan"]
    professional = await by_code(admin_client, "professional")

    used = await admin_client.delete(f"/api/admin/plans/{professional['id']}")
    unused = await admin_client.delete(f"/api/admin/plans/{created['id']}")

    assert used.status_code == 409 and used.json()["detail"] == "This plan has been used. Retire it instead."
    assert unused.status_code == 204
    assert [plan["code"] for plan in await plans(admin_client)] == ["starter", "standard", "professional"]
    assert [event.detail["code"] for event in await events("plan.deleted")] == ["lite"]


async def test_a_plan_change_reaches_the_unlock_hints_at_once(admin_client: ApiClient) -> None:
    standard = await by_code(admin_client, "standard")
    async with db.SessionLocal() as session:
        assert (await entitlements.unlock(session)).files == "Standard"

    await admin_client.patch(f"/api/admin/plans/{standard['id']}", json={"name": "Standard Plus"})

    async with db.SessionLocal() as session:
        assert (await entitlements.unlock(session)).files == "Standard Plus"


async def test_the_public_pages_read_plans_and_the_business_without_a_session(
    client: ApiClient, admin_client: ApiClient
) -> None:
    settings = (await admin_client.get("/api/admin/billing/config")).json()["settings"]
    await admin_client.put(
        "/api/admin/billing/settings",
        json={**settings, "business_name": "Example Labs", "address": "  ", "contact_phone": "+91 1"},
    )

    site = await client.get("/api/public/site")
    public = await client.get("/api/public/plans")

    assert site.headers["cache-control"] == "public, max-age=60"
    assert site.json() == {
        "business_name": "Example Labs",
        "contact_email": None,
        "contact_phone": "+91 1",
        "address": None,
    }
    assert public.headers["cache-control"] == "public, max-age=60"
    starter = public.json()[0]
    assert set(starter) == {
        "code",
        "name",
        "description",
        "price_paise",
        "recommended",
        "features",
        "plugins",
        "limits",
        "chat_models",
        "all_chat_models",
        "default_chat_model",
        "answers_estimate",
    }
    assert starter["answers_estimate"] == 1_600 and starter["default_chat_model"]["allowance_rate"] == 0.05
    assert [model["display_name"] for model in starter["chat_models"]] == ["Claude Haiku 5.5", "GPT-5.4 mini"]
    assert starter["plugins"] == []  # no plugin is registered in these tests
    professional = public.json()[2]
    assert professional["all_chat_models"] is True and professional["default_chat_model"]["display_name"] == (
        "Claude Opus 5.5"
    )
    assert professional["answers_estimate"] == 290


async def test_plans_are_the_admins(create_user: CreateUser) -> None:
    user = await create_user("eve")

    assert (await user.get("/api/admin/plans")).status_code == 403
    assert (await user.post("/api/admin/plans", json=new_plan())).status_code == 403
    async with db.SessionLocal() as session:
        assert (await session.get(User, user.user_id)) is not None
