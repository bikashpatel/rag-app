import MarkTile, { RIM_SKY, useMarkIds, type MarkProps } from "./MarkTile";

/** Checking a payment: a glowing ring circles the rupee and pings outward, quietly saying "working on it". */
export default function PaymentCheckMark(props: MarkProps) {
  const ids = useMarkIds();
  return (
    <MarkTile
      {...props}
      kind="payment-check"
      ids={ids}
      rim={RIM_SKY}
      glow={3}
      defs={
        <linearGradient id={ids.id("ring")} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#67e8f9" />
          <stop offset="1" stopColor="#a78bfa" />
        </linearGradient>
      }
    >
      <circle
        className="mark-payment-check-ping"
        cx="60"
        cy="60"
        r="30"
        fill="none"
        stroke="#a78bfa"
        strokeOpacity=".5"
        strokeWidth="1"
      />
      <circle
        className="mark-payment-check-ring"
        cx="60"
        cy="60"
        r="26"
        fill="none"
        stroke={ids.url("ring")}
        strokeWidth="4"
        strokeLinecap="round"
        strokeDasharray="30 12"
        filter={ids.url("glow")}
      />
      <circle cx="60" cy="60" r="17" fill="#1e2a6b" />
      <text x="60" y="67" textAnchor="middle" fontSize="20" fontWeight="800" fill="#e0f2fe" fontFamily="system-ui">
        ₹
      </text>
    </MarkTile>
  );
}
