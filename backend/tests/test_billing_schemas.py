"""The request rules of the billing contract (app/schemas_billing.py; docs/briefs/plans-billing.md 3.1, 7.1): what a
plan, a checkout, a profile, the billing settings and the admin's billing actions accept, before any router runs."""

from datetime import date, datetime, timezone
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.schemas import MAX_INT
from app.schemas_billing import (
    MAX_BIGINT,
    BillingProfile,
    BillingSettings,
    ChangePlanRequest,
    CheckoutRequest,
    EndComplimentaryRequest,
    GrantRequest,
    OfflinePaymentRequest,
    PlanAccess,
    PlanCreate,
    PlanInput,
    PlanSnapshot,
    PlanUpdate,
    RefundRequest,
    ResolveRequest,
    RevokeRequest,
    UserPlanRow,
    check_plan,
)

LIMITS = {
    "monthly_token_limit": 500_000,
    "rate_limit_per_min": 10,
    "max_upload_mb": 10,
    "max_documents": 50,
    "max_storage_mb": 200,
    "monthly_image_limit": 30,
    "vision_monthly_token_limit": 150_000,
}
STARTER = {
    "name": "Starter",
    "description": "Ask questions across your documents, for everyday use.",
    "price_paise": 50_000,
    "rank": 10,
    "status": "active",
    "recommended": False,
    "features": {"files": False, "images": False, "plugins": []},
    "limits": LIMITS,
    "chat_model_ids": [4, 3],
    "default_chat_model_id": 4,
}
SETTINGS = {
    "business_name": "RAG Assistant",
    "address": "12 MG Road\nBengaluru 560001",
    "contact_email": "billing@example.com",
    "contact_phone": "+91 80 1234 5678",
    "receipt_prefix": "RA",
    "upi_limit_paise": 200_000,
}


def refused(model: type[BaseModel], body: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(body)
    return str(caught.value)


# ---------------- Plans ----------------


def test_a_plan_is_accepted_with_names_trimmed_and_lists_deduplicated() -> None:
    plan = PlanCreate.model_validate(
        {
            **STARTER,
            "code": "starter",
            "name": "  Starter ",
            "chat_model_ids": [4, 3, 4],
            "features": {"files": False, "images": False, "plugins": ["gmail", "gmail"]},
        }
    )

    assert (plan.code, plan.name, plan.chat_model_ids, plan.features.plugins) == (
        "starter",
        "Starter",
        [4, 3],
        ["gmail"],
    )
    assert plan.limits.model_dump() == LIMITS


def test_every_chat_model_and_no_default_is_a_valid_plan() -> None:
    plan = PlanInput.model_validate({**STARTER, "chat_model_ids": None, "default_chat_model_id": None})

    assert (plan.chat_model_ids, plan.default_chat_model_id) == (None, None)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"price_paise": 50_050}, "whole number of rupees"),
        ({"price_paise": 0}, "greater than or equal to 100"),
        ({"price_paise": 10_000_100}, "less than or equal to 10000000"),
        ({"price_paise": 500.0}, "valid integer"),
        ({"price_paise": "50000"}, "valid integer"),
        ({"rank": -1}, "greater than or equal to 0"),
        ({"rank": True}, "valid integer"),
        ({"name": ""}, "at least 1 character"),
        ({"name": "x" * 61}, "at most 60 characters"),
        ({"name": "Star\nter"}, "control characters"),
        ({"description": "x" * 301}, "at most 300 characters"),
        ({"description": "two\nlines"}, "control characters"),
        ({"status": "hidden"}, "'active' or 'retired'"),
        ({"recommended": "yes"}, "valid boolean"),
        ({"recommended": True, "status": "retired"}, "A retired plan can't be the recommended one."),
        ({"default_chat_model_id": 9}, "The default chat model must be one of the plan's models."),
        ({"chat_model_ids": [0]}, "greater than or equal to 1"),
        ({"chat_model_ids": [1] * 1001}, "at most 1000 items"),
        ({"features": {"files": False, "images": False, "plugins": ["Gmail!"]}}, "should match pattern"),
        ({"features": {"files": 1, "images": False, "plugins": []}}, "valid boolean"),
        ({"features": {"files": False, "images": False}}, "Field required"),
        ({"features": {"files": False, "images": False, "plugins": [], "gmail": True}}, "Extra inputs"),
        ({"limits": {**LIMITS, "max_upload_mb": 0}}, "greater than or equal to 1"),
        ({"limits": {**LIMITS, "max_upload_mb": settings.max_upload_mb_hard + 1}}, "less than or equal to"),
        ({"limits": {**LIMITS, "max_documents": -1}}, "greater than or equal to 0"),
        ({"limits": {**LIMITS, "monthly_token_limit": MAX_BIGINT + 1}}, "less than or equal to"),
        ({"limits": {**LIMITS, "rate_limit_per_min": MAX_INT + 1}}, "less than or equal to"),
        ({"limits": {k: v for k, v in LIMITS.items() if k != "vision_monthly_token_limit"}}, "Field required"),
        ({"code": "starter"}, "Extra inputs"),
    ],
)
def test_a_plan_breaking_a_rule_is_refused(change: dict[str, Any], message: str) -> None:
    assert message in refused(PlanInput, {**STARTER, **change})


@pytest.mark.parametrize("code", ["Starter", "s", "9lives", "pro plan", "a" * 33, ""])
def test_a_plan_code_must_be_a_lowercase_slug(code: str) -> None:
    assert "should match pattern" in refused(PlanCreate, {**STARTER, "code": code})


def test_a_plan_update_changes_only_what_it_sends() -> None:
    update = PlanUpdate.model_validate({"price_paise": 60_000, "chat_model_ids": None, "apply_to_current": True})

    assert update.model_fields_set == {"price_paise", "chat_model_ids", "apply_to_current"}
    assert (update.price_paise, update.chat_model_ids, update.confirm_reduction) == (60_000, None, False)


@pytest.mark.parametrize(
    "field", ["name", "description", "price_paise", "rank", "status", "recommended", "features", "limits"]
)
def test_a_plan_update_refuses_null_for_fields_without_a_null_meaning(field: str) -> None:
    assert f"{field} can't be null" in refused(PlanUpdate, {field: None})


def test_a_plan_update_refuses_unknown_fields_and_a_bad_price() -> None:
    assert "Extra inputs" in refused(PlanUpdate, {"code": "pro"})
    assert "whole number of rupees" in refused(PlanUpdate, {"price_paise": 150})


def test_the_rules_between_fields_apply_to_a_merged_plan_too() -> None:
    check_plan("active", True, [1, 2], 2)
    check_plan("retired", False, None, 5)
    with pytest.raises(ValueError, match="retired plan"):
        check_plan("retired", True, None, None)
    with pytest.raises(ValueError, match="default chat model"):
        check_plan("active", False, [1, 2], 3)


def test_a_snapshot_keeps_every_limit_through_json() -> None:
    snapshot = PlanSnapshot.model_validate(
        {
            "plan_id": 1,
            "code": "starter",
            "name": "Starter",
            "features": {"files": False, "images": False, "plugins": []},
            "limits": {**LIMITS, "vision_monthly_token_limit": 0},
            "restrict_models": True,
            "chat_model_ids": [4, 3],
            "default_chat_model_id": 4,
        }
    )

    stored = snapshot.model_dump(mode="json")

    assert stored["limits"] == {**LIMITS, "vision_monthly_token_limit": 0}
    assert PlanSnapshot.model_validate(stored) == snapshot


# ---------------- The user's ----------------


def test_a_checkout_needs_the_terms_accepted_as_a_json_true() -> None:
    assert CheckoutRequest.model_validate({"plan_id": 2, "kind": "now", "accept_terms": True}).kind == "now"
    assert "Accept the terms to continue." in refused(
        CheckoutRequest, {"plan_id": 2, "kind": "period", "accept_terms": False}
    )
    for value in (1, "true"):
        assert "valid boolean" in refused(CheckoutRequest, {"plan_id": 2, "kind": "period", "accept_terms": value})
    assert "Field required" in refused(CheckoutRequest, {"plan_id": 2, "kind": "period"})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "renew"}, "'period' or 'now'"),
        ({"plan_id": "2"}, "valid integer"),
        ({"plan_id": 0}, "greater than or equal to 1"),
        ({"amount_paise": 100}, "Extra inputs"),
    ],
)
def test_a_checkout_takes_only_the_plan_and_the_kind(change: dict[str, Any], message: str) -> None:
    assert message in refused(CheckoutRequest, {"plan_id": 2, "kind": "period", "accept_terms": True, **change})


def test_a_profile_turns_blank_values_into_null() -> None:
    profile = BillingProfile.model_validate({"name": "  Maya Chen ", "email": "   "})

    assert (profile.name, profile.email) == ("Maya Chen", None)
    assert BillingProfile.model_validate({"name": "", "email": " maya@example.com "}).model_dump() == {
        "name": None,
        "email": "maya@example.com",
    }


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"name": None, "email": "maya@example"}, "should match pattern"),
        ({"name": None, "email": "maya @example.com"}, "should match pattern"),
        ({"name": None, "email": "m@" + "e" * 250 + ".in"}, "at most 254 characters"),
        ({"name": "x" * 101, "email": None}, "at most 100 characters"),
        ({"name": "Maya\tChen", "email": None}, "control characters"),
        ({"name": None}, "Field required"),
        ({"name": None, "email": None, "phone": "1"}, "Extra inputs"),
    ],
)
def test_a_profile_with_a_bad_value_is_refused(body: dict[str, Any], message: str) -> None:
    assert message in refused(BillingProfile, body)


def test_plan_access_reports_ist_dates_and_utc_instants() -> None:
    access = PlanAccess.model_validate(
        {
            "state": "active",
            "access": True,
            "plan": {"id": 2, "code": "standard", "name": "Standard"},
            "complimentary": False,
            "open_ended": False,
            "test_mode": False,
            "period_start": datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc),
            "period_end": datetime(2026, 11, 25, 18, 30, tzinfo=timezone.utc),
            "paid_through": datetime(2026, 11, 25, 18, 30, tzinfo=timezone.utc),
            "valid_through": date(2026, 11, 25),
            "next_plan": None,
            "expiring_soon": False,
            "ended_at": None,
            "features": {"files": True, "images": False, "plugins": []},
            "pending_order": None,
            "can_start_new_period": False,
            "unlock": {"files": None, "images": "Professional", "plugins": {"gmail": "Professional"}, "models": None},
        }
    ).model_dump(mode="json")

    assert (access["valid_through"], access["period_end"]) == ("2026-11-25", "2026-11-25T18:30:00Z")
    row = UserPlanRow.model_validate(
        {"user": {"id": None, "username": "maya"}, "plan": None, "date": "2026-11-25", "last_payment_at": None}
    )
    assert row.model_dump(mode="json")["date"] == "2026-11-25"


# ---------------- The admin's ----------------


def test_billing_settings_trim_blank_contacts_to_null_and_keep_a_multiline_address() -> None:
    saved = BillingSettings.model_validate(
        {**SETTINGS, "contact_email": " ", "contact_phone": "", "business_name": " RA "}
    )

    assert (saved.business_name, saved.contact_email, saved.contact_phone) == ("RA", None, None)
    assert saved.address == "12 MG Road\nBengaluru 560001"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"receipt_prefix": "TEST"}, "TEST is reserved for test-mode receipts"),
        ({"receipt_prefix": "ra"}, "should match pattern"),
        ({"receipt_prefix": "RAGAP"}, "should match pattern"),
        ({"business_name": ""}, "at least 1 character"),
        ({"address": "12 MG Road\x00"}, "control characters"),
        ({"address": "x" * 501}, "at most 500 characters"),
        ({"contact_email": "billing"}, "should match pattern"),
        ({"contact_phone": "0" * 21}, "at most 20 characters"),
        ({"upi_limit_paise": -1}, "greater than or equal to 0"),
        ({"upi_limit_paise": 10_000_001}, "less than or equal to 10000000"),
        ({"gstin": "29ABCDE1234F1Z5"}, "Extra inputs"),
    ],
)
def test_billing_settings_with_a_bad_value_are_refused(change: dict[str, Any], message: str) -> None:
    assert message in refused(BillingSettings, {**SETTINGS, **change})


def test_a_refund_is_all_that_is_refundable_or_at_least_one_rupee() -> None:
    refund = RefundRequest.model_validate({"amount_paise": None, "reason": " Asked for it ", "end_access": False})

    assert (refund.amount_paise, refund.reason) == (None, "Asked for it")
    assert "greater than or equal to 100" in refused(
        RefundRequest, {"amount_paise": 99, "reason": "x", "end_access": True}
    )
    assert "at least 1 character" in refused(RefundRequest, {"amount_paise": None, "reason": "  ", "end_access": True})
    assert "Field required" in refused(RefundRequest, {"amount_paise": None, "reason": "x"})


def test_a_bank_transfer_needs_a_rupee_a_date_and_a_reference() -> None:
    body = {
        "user_id": 7,
        "plan_id": 2,
        "amount_paise": 200_000,
        "received_on": "2026-10-24",
        "reference": " UTR123 ",
        "note": " ",
        "apply": True,
    }
    payment = OfflinePaymentRequest.model_validate(body)

    assert (payment.received_on, payment.reference, payment.note) == (date(2026, 10, 24), "UTR123", None)
    assert OfflinePaymentRequest.model_validate({**body, "note": "Paid\nin two parts"}).note == "Paid\nin two parts"
    assert "greater than or equal to 100" in refused(OfflinePaymentRequest, {**body, "amount_paise": 0})
    assert "valid date" in refused(OfflinePaymentRequest, {**body, "received_on": "24/10/2026"})
    assert "at most 64 characters" in refused(OfflinePaymentRequest, {**body, "reference": "x" * 65})
    assert "at most 500 characters" in refused(OfflinePaymentRequest, {**body, "note": "x" * 501})


def test_a_grant_is_dated_up_to_90_days_or_open_ended() -> None:
    grant = GrantRequest.model_validate({"plan_id": 3, "days": None, "reason": "Owner", "start": "now"})

    assert grant.days is None
    assert (
        GrantRequest.model_validate({"plan_id": 3, "days": 90, "reason": "Trial", "start": "after_current"}).days == 90
    )
    for days in (0, 91):
        assert "days" in refused(GrantRequest, {"plan_id": 3, "days": days, "reason": "Trial", "start": "now"})
    assert "Field required" in refused(GrantRequest, {"plan_id": 3, "days": 30, "reason": "Trial"})
    assert "'now' or 'after_current'" in refused(
        GrantRequest, {"plan_id": 3, "days": 30, "reason": "x", "start": "later"}
    )


@pytest.mark.parametrize(
    ("model", "body"),
    [
        (ChangePlanRequest, {"plan_id": 2}),
        (RevokeRequest, {}),
        (EndComplimentaryRequest, {}),
    ],
)
def test_admin_changes_need_a_short_reason(model: type[BaseModel], body: dict[str, Any]) -> None:
    assert model.model_validate({**body, "reason": "Goodwill"}).model_dump()["reason"] == "Goodwill"
    assert "at most 200 characters" in refused(model, {**body, "reason": "x" * 201})
    assert "at least 1 character" in refused(model, {**body, "reason": ""})
    assert "Extra inputs" in refused(model, {**body, "reason": "x", "notify": True})


def test_resolving_a_review_needs_a_note() -> None:
    assert (
        ResolveRequest.model_validate({"note": "Refunded by phone\nthen granted"}).note
        == "Refunded by phone\nthen granted"
    )
    assert "at least 1 character" in refused(ResolveRequest, {"note": "   "})
    assert "control characters" in refused(ResolveRequest, {"note": "bell\x07"})
