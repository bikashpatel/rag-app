import MarkTile, { RIM_SKY, useMarkIds, type MarkProps } from "./MarkTile";

const SHIELD = "M60 26l26 10v18c0 18-11 31-26 38-15-7-26-20-26-38V36z";

/** Account security: a glowing shield with a keyhole, pulsing gently (passwords and sign-in). */
export default function SecurityMark(props: MarkProps) {
  const ids = useMarkIds();
  return (
    <MarkTile
      {...props}
      kind="security"
      ids={ids}
      rim={RIM_SKY}
      glow={3}
      defs={
        <linearGradient id={ids.id("shield")} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#67e8f9" />
          <stop offset="1" stopColor="#818cf8" />
        </linearGradient>
      }
    >
      <path
        className="mark-security-ping"
        d={SHIELD}
        fill="none"
        stroke="#67e8f9"
        strokeOpacity=".6"
        strokeWidth="1"
      />
      <path d={SHIELD} fill={ids.url("shield")} filter={ids.url("glow")} />
      <g className="mark-security-keyhole" fill="#1e2a6b">
        <circle cx="60" cy="54" r="7" />
        <path d="M57 58h6l2 14h-10z" />
      </g>
    </MarkTile>
  );
}
