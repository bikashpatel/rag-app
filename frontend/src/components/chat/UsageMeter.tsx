import type { Me } from "../../api/types";
import { formatCompact, formatNumber } from "../../lib/format";

type UsageMeterProps = {
  user: Me;
  /** A chat model the user can pick generates images: show the images row even without a limit. */
  imagesOffered: boolean;
};

/**
 * This month's chat tokens against the monthly budget, and generated images against the monthly image limit (0 =
 * unlimited; the admin has neither). The images row shows when there is a limit, images were made this month, or
 * images can be made.
 */
export default function UsageMeter({ user, imagesOffered }: UsageMeterProps) {
  const imageLimit = user.is_admin ? 0 : user.limits.monthly_image_limit;
  const showImages = imageLimit > 0 || user.usage.images > 0 || imagesOffered;
  return (
    <div className="chat-usage">
      <UsageRow
        label="Tokens this month"
        meterLabel="Tokens used this month"
        noun="tokens"
        used={user.usage.chat_tokens}
        limit={user.is_admin ? 0 : user.limits.monthly_token_limit}
        format={formatCompact}
      />
      {showImages ? (
        <UsageRow
          label="Images this month"
          meterLabel="Images generated this month"
          noun="images"
          used={user.usage.images}
          limit={imageLimit}
          format={formatNumber}
        />
      ) : null}
    </div>
  );
}

type UsageRowProps = {
  label: string;
  meterLabel: string;
  noun: string;
  used: number;
  limit: number;
  format: (n: number) => string;
};

function UsageRow({ label, meterLabel, noun, used, limit, format }: UsageRowProps) {
  const ratio = limit > 0 ? Math.min(used / limit, 1) : 0;
  const barClass =
    ratio >= 1 ? "chat-usage-bar chat-usage-danger" : ratio >= 0.9 ? "chat-usage-bar chat-usage-warning" : "chat-usage-bar";

  return (
    <div className="chat-usage-item">
      <div className="chat-usage-row">
        <span>{label}</span>
        {limit > 0 ? (
          <span className="chat-usage-value">
            {format(used)} / {format(limit)}
          </span>
        ) : (
          <span className="chat-usage-value">
            {format(used)} <span className="chat-usage-unlimited">· Unlimited</span>
          </span>
        )}
      </div>
      {limit > 0 ? (
        <div
          className={barClass}
          role="meter"
          aria-label={meterLabel}
          aria-valuemin={0}
          aria-valuemax={limit}
          aria-valuenow={Math.min(used, limit)}
          aria-valuetext={`${formatNumber(used)} of ${formatNumber(limit)} ${noun}`}
        >
          <span style={{ width: `${ratio * 100}%` }} />
        </div>
      ) : null}
    </div>
  );
}
