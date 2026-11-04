import MarkTile, { RIM_VIOLET, useMarkIds, type MarkProps } from "./MarkTile";

/** Pay with UPI: a light scans the QR code on a phone while a rupee coin flips beside it. */
export default function UpiPayMark(props: MarkProps) {
  const ids = useMarkIds();
  return (
    <MarkTile
      {...props}
      kind="upi-pay"
      ids={ids}
      rim={RIM_VIOLET}
      glow={2.5}
      defs={
        <clipPath id={ids.id("screen")}>
          <rect x="44" y="34" width="32" height="40" rx="3" />
        </clipPath>
      }
    >
      <rect x="38" y="24" width="44" height="74" rx="9" fill="#111a44" stroke="#93c5fd" strokeWidth="2" />
      <g fill="#e0f2fe">
        <rect x="47" y="38" width="9" height="9" rx="1" />
        <rect x="64" y="38" width="9" height="9" rx="1" />
        <rect x="47" y="61" width="9" height="9" rx="1" />
        <rect x="59" y="50" width="3" height="3" />
        <rect x="64" y="56" width="4" height="4" />
        <rect x="69" y="62" width="4" height="4" />
        <rect x="60" y="64" width="3" height="5" />
        <rect x="66" y="50" width="3" height="3" />
      </g>
      <g clipPath={ids.url("screen")}>
        <rect
          className="mark-upi-pay-scan"
          x="42"
          y="52"
          width="36"
          height="2.5"
          fill="#67e8f9"
          filter={ids.url("glow")}
        />
      </g>
      <g className="mark-upi-pay-coin">
        <circle cx="88" cy="80" r="11" fill="#fbbf24" stroke="#fde68a" strokeWidth="2" />
        <text x="88" y="85" textAnchor="middle" fontSize="13" fontWeight="800" fill="#92400e" fontFamily="system-ui">
          ₹
        </text>
      </g>
    </MarkTile>
  );
}
