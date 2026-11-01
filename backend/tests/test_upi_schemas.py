"""The direct-UPI request rules (schemas_billing.py; docs/briefs/upi-direct.md D1, D5 and D6) beyond the ones in
test_billing_schemas.py: the UPI IDs real apps hand out, the limits of the ID and the payee name, the admin's requests,
and the settings through the API (refused without both fields, audited with the values before and after)."""

from typing import Any

import pytest
from billing_support import events
from conftest import ApiClient
from pydantic import BaseModel, ValidationError
from upi_support import CONTACT, PAYEE, UPI_ID

from app.schemas_billing import BillingSettings, UpiConfirmRequest, UpiRejectRequest

SETTINGS = {
    "business_name": "Asha Rao Consulting",
    "address": "",
    "contact_email": None,
    "contact_phone": None,
    "receipt_prefix": "RA",
    "upi_limit_paise": 200_000,
}


def refused(model: type[BaseModel], data: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(data)
    return str(caught.value)


@pytest.mark.parametrize(
    "upi_id",
    [
        "q123456789@ybl",
        "paytmqr2810050501abcd@paytm",
        "asha.rao-1_x@okicici",
        "9876543210@upi",
        f"{'a' * 80}@{'b' * 19}",  # 100 characters
        f"a@b{'c' * 63}",
    ],
)
def test_the_upi_ids_apps_hand_out_are_accepted(upi_id: str) -> None:
    saved = BillingSettings.model_validate({**SETTINGS, "upi_id": upi_id})

    assert saved.upi_id == upi_id


@pytest.mark.parametrize(
    "upi_id",
    [
        f"{'a' * 80}@{'b' * 20}",  # 101 characters
        f"a@b{'c' * 64}",  # a handle of 65
        ".asha@ybl",
        "asha@y",
        "asha@ybl.com",
        "@ybl",
        "asha@",
        "asha%40ybl",
        "asha\n@ybl",
        "asha@ybl x",
    ],
)
def test_upi_ids_that_arent_upi_ids_are_refused(upi_id: str) -> None:
    assert "upi_id" in refused(BillingSettings, {**SETTINGS, "upi_id": upi_id})


def test_payee_names_keep_the_allowed_punctuation_up_to_fifty_characters() -> None:
    name = "Asha Rao & Co. (Pvt), A/B-1's"
    saved = BillingSettings.model_validate({**SETTINGS, "upi_payee_name": name})
    longest = BillingSettings.model_validate({**SETTINGS, "upi_payee_name": " " + "A" * 50 + " "})

    assert saved.upi_payee_name == name
    assert longest.upi_payee_name == "A" * 50
    for bad in ("Asha\tRao!", "Asha_Rao", "Asha@Rao", "Asha;Rao", "Asha+Rao", "Asha%20Rao"):
        assert "upi_payee_name" in refused(BillingSettings, {**SETTINGS, "upi_payee_name": bad})


def test_the_admins_requests_refuse_extra_fields_and_long_notes() -> None:
    assert "Extra inputs" in refused(UpiConfirmRequest, {"utr": "630512345678", "amount": 1})
    assert "Extra inputs" in refused(UpiRejectRequest, {"reason": "other", "note": "x", "utr": "630512345678"})
    assert "at most 200 characters" in refused(UpiRejectRequest, {"reason": "other", "note": "x" * 201})
    assert "Field required" in refused(UpiConfirmRequest, {})


async def test_the_settings_api_refuses_upi_without_both_fields_and_audits_a_change(
    admin_client: ApiClient,
) -> None:
    config = (await admin_client.get("/api/admin/billing/config")).json()
    base = {**config["settings"], "contact_email": CONTACT}

    missing = await admin_client.put("/api/admin/billing/settings", json={**base, "upi_enabled": True})
    saved = await admin_client.put(
        "/api/admin/billing/settings",
        json={**base, "upi_enabled": True, "upi_id": UPI_ID.upper(), "upi_payee_name": f"  {PAYEE}  "},
    )

    assert missing.status_code == 422
    assert "Add your UPI ID and the payee name to turn on UPI payments." in missing.text
    assert saved.status_code == 200, saved.text
    settings = saved.json()["settings"]
    assert (settings["upi_enabled"], settings["upi_id"], settings["upi_payee_name"]) == (True, UPI_ID, PAYEE)
    (updated,) = await events("settings.updated")
    assert updated.actor == "admin"
    assert updated.detail["changed"]["upi_id"] == {"before": None, "after": UPI_ID}
    assert updated.detail["changed"]["upi_enabled"] == {"before": False, "after": True}
