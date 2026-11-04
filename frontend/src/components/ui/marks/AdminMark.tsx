import MarkTile, { RIM_VIOLET, useMarkIds, type MarkProps } from "./MarkTile";

/** Admin: bars rise and fall under a live trend line, a dashboard at a glance. */
export default function AdminMark(props: MarkProps) {
  const ids = useMarkIds();
  return (
    <MarkTile
      {...props}
      kind="admin"
      ids={ids}
      rim={RIM_VIOLET}
      glow={2}
      defs={
        <linearGradient id={ids.id("bar")} x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="#6366f1" />
          <stop offset="1" stopColor="#67e8f9" />
        </linearGradient>
      }
    >
      <path d="M30 86h60" stroke="#475569" strokeWidth="2" strokeLinecap="round" />
      <rect className="mark-admin-bar-1" x="36" y="44" width="11" height="42" rx="3" fill={ids.url("bar")} />
      <rect className="mark-admin-bar-2" x="54" y="38" width="11" height="48" rx="3" fill={ids.url("bar")} />
      <rect className="mark-admin-bar-3" x="72" y="30" width="11" height="56" rx="3" fill={ids.url("bar")} />
      <path
        className="mark-admin-trend"
        d="M32 62l16-10 18 6 22-20"
        fill="none"
        stroke="#fde68a"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray="6 5"
        filter={ids.url("glow")}
      />
      <circle cx="88" cy="38" r="3.5" fill="#fde68a" />
      <circle className="mark-admin-ping" cx="88" cy="38" r="3.5" fill="none" stroke="#fde68a" strokeWidth="1" />
    </MarkTile>
  );
}
