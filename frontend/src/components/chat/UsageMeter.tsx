import type { Me } from "../../api/types";
import { formatCompact, formatNumber } from "../../lib/format";

/** This month's chat tokens against the monthly budget (0 = unlimited; the admin has no budget). */
export default function UsageMeter({ user }: { user: Me }) {
  const used = user.usage.chat_tokens;
  const limit = user.is_admin ? 0 : user.limits.monthly_token_limit;
  const ratio = limit > 0 ? Math.min(used / limit, 1) : 0;
  const barClass =
    ratio >= 1 ? "chat-usage-bar chat-usage-danger" : ratio >= 0.9 ? "chat-usage-bar chat-usage-warning" : "chat-usage-bar";

  return (
    <div className="chat-usage">
      <div className="chat-usage-row">
        <span>Tokens this month</span>
        {limit > 0 ? (
          <span className="chat-usage-value">
            {formatCompact(used)} / {formatCompact(limit)}
          </span>
        ) : (
          <span className="chat-usage-value">
            {formatCompact(used)} <span className="chat-usage-unlimited">· Unlimited</span>
          </span>
        )}
      </div>
      {limit > 0 ? (
        <div
          className={barClass}
          role="meter"
          aria-label="Tokens used this month"
          aria-valuemin={0}
          aria-valuemax={limit}
          aria-valuenow={Math.min(used, limit)}
          aria-valuetext={`${formatNumber(used)} of ${formatNumber(limit)} tokens`}
        >
          <span style={{ width: `${ratio * 100}%` }} />
        </div>
      ) : null}
    </div>
  );
}
