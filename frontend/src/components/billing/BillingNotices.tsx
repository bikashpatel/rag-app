import { Link } from "react-router-dom";
import type { PaymentsInfo } from "../../api/types";
import { FlaskIcon, InfoIcon } from "../ui/icons";

/** "Test mode: no real money moves. …", shown to testers while the server runs test keys (6.9, visual 3.4). */
export function TestModeNotice() {
  return (
    <div className="ui-notice ui-notice-warning" role="status">
      <FlaskIcon size={15} />
      <span className="ui-notice-text">
        <b>Test mode:</b> no real money moves. Pay with card <span className="plans-mono">4100 2800 0000 1007</span>,
        any future expiry and any CVV.
      </span>
    </div>
  );
}

/** "Online payment isn't set up yet." with the contact email, or for the admin the way to set it up. */
export function PaymentsUnavailable({ payments, admin }: { payments: PaymentsInfo; admin: boolean }) {
  return (
    <div className="ui-notice" role="status">
      <InfoIcon size={15} />
      <span className="ui-notice-text">
        Online payment isn't set up yet.{" "}
        {admin ? (
          <Link to="/admin#billing">Set up payments in Admin → Billing</Link>
        ) : payments.contact_email ? (
          <>
            Contact <a href={`mailto:${payments.contact_email}`}>{payments.contact_email}</a>.
          </>
        ) : null}
      </span>
    </div>
  );
}
