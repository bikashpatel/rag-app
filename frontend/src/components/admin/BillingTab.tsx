import { useCallback, useState } from "react";
import type { BillingAdminConfig } from "../../api/types";
import { formatInr } from "../../lib/billing";
import SegmentedControl from "../ui/SegmentedControl";
import { BILLING_CONFIG_PATH } from "./adminApi";
import BillingBusiness from "./BillingBusiness";
import BillingOrders from "./BillingOrders";
import BillingOverview from "./BillingOverview";
import BillingPayments from "./BillingPayments";
import { currentIstMonth } from "./billingUi";
import FlashMessage from "./FlashMessage";
import { useFlash, useResource } from "./hooks";
import MonthPicker from "./MonthPicker";
import RecordTransferModal from "./RecordTransferModal";

const SECTIONS = [
  { value: "overview", label: "Overview" },
  { value: "payments", label: "Payments" },
  { value: "orders", label: "Orders" },
  { value: "business", label: "Business & payments" },
] as const;

type Section = (typeof SECTIONS)[number]["value"];

/** A positive whole number from the hash, else null. */
function idParam(value: string | null): number | null {
  const id = Number(value);
  return value && Number.isSafeInteger(id) && id > 0 ? id : null;
}

type BillingTabProps = {
  /** From the hash: "#billing/<section>?<params>". */
  section: string | null;
  params: URLSearchParams;
  onNavigate: (hash: string) => void;
};

/** Admin → Billing: the month's money and what needs attention, payments, orders, and the business settings (8.11). */
export default function BillingTab({ section, params, onNavigate }: BillingTabProps) {
  const current: Section = SECTIONS.find((option) => option.value === section)?.value ?? "overview";
  const [month, setMonth] = useState(currentIstMonth);
  const [recording, setRecording] = useState(false);
  const [version, setVersion] = useState(0);
  const config = useResource<BillingAdminConfig>(BILLING_CONFIG_PATH, "Couldn't load the payments setup.");
  const flash = useFlash();
  const paramsRead = useCallback(() => onNavigate(`billing/${current}`), [onNavigate, current]);

  return (
    <div className="admin-stack">
      <div className="admin-billing-bar">
        <SegmentedControl
          label="Billing section"
          size="sm"
          value={current}
          options={SECTIONS}
          onChange={(value) => onNavigate(`billing/${value}`)}
        />
        {current === "overview" || current === "payments" ? (
          <MonthPicker month={month} onChange={setMonth} latest={currentIstMonth()} label="Month (IST)" />
        ) : null}
      </div>
      <FlashMessage flash={flash} />
      {current === "overview" ? (
        <BillingOverview
          month={month}
          config={config}
          onNavigate={onNavigate}
          onRecord={() => setRecording(true)}
          version={version}
        />
      ) : null}
      {current === "payments" ? (
        <BillingPayments
          month={month}
          userParam={idParam(params.get("user"))}
          paymentParam={idParam(params.get("payment"))}
          onParamsRead={paramsRead}
          onRecord={() => setRecording(true)}
          version={version}
        />
      ) : null}
      {current === "orders" ? (
        <BillingOrders
          orderParam={params.get("order")}
          onParamsRead={paramsRead}
          onGrant={(userId) => onNavigate(`users?user=${userId}&grant=1`)}
        />
      ) : null}
      {current === "business" ? <BillingBusiness config={config} onNavigate={onNavigate} /> : null}
      {recording ? (
        <RecordTransferModal
          onClose={() => setRecording(false)}
          onRecorded={(payment) => {
            setRecording(false);
            setVersion((n) => n + 1);
            flash.success(`Recorded ${formatInr(payment.amount_paise)} from ${payment.user.username}.`);
          }}
        />
      ) : null}
    </div>
  );
}
