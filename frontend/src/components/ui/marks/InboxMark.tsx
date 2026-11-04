import MarkTile, { RIM_VIOLET, useMarkIds, type MarkProps } from "./MarkTile";

/** Check your inbox: a letter lifts out of an envelope while a small paper plane flies off. */
export default function InboxMark(props: MarkProps) {
  const ids = useMarkIds();
  return (
    <MarkTile
      {...props}
      kind="inbox"
      ids={ids}
      rim={RIM_VIOLET}
      glow={2.5}
      defs={
        <clipPath id={ids.id("clip")}>
          <rect x="30" y="20" width="60" height="64" />
        </clipPath>
      }
    >
      <g clipPath={ids.url("clip")}>
        <g className="mark-inbox-letter">
          <rect x="40" y="44" width="40" height="34" rx="4" fill="#f8fafc" />
          <rect x="46" y="51" width="22" height="3" rx="1.5" fill="#818cf8" />
          <rect x="46" y="58" width="28" height="3" rx="1.5" fill="#cbd5e1" />
          <rect x="46" y="65" width="18" height="3" rx="1.5" fill="#cbd5e1" />
        </g>
      </g>
      <path d="M32 64l28 18 28-18v24a4 4 0 0 1-4 4H36a4 4 0 0 1-4-4z" fill="#6366f1" />
      <path d="M32 88l22-16M88 88l-22-16" stroke="#a5b4fc" strokeWidth="2" />
      <path className="mark-inbox-plane" d="M78 40l16-7-7 16-3-6z" fill="#67e8f9" filter={ids.url("glow")} />
    </MarkTile>
  );
}
