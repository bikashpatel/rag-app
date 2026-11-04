import MarkTile, { RIM_SKY, useMarkIds, type MarkProps } from "./MarkTile";

const GEM = "M36 50l12-16h24l12 16-24 34z";

/** Plans: a glowing gem with light sweeping across its facets and two twinkling stars. */
export default function PlansMark(props: MarkProps) {
  const ids = useMarkIds();
  return (
    <MarkTile
      {...props}
      kind="plans"
      ids={ids}
      rim={RIM_SKY}
      glow={3.5}
      defs={
        <>
          <linearGradient id={ids.id("gem")} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#67e8f9" />
            <stop offset=".5" stopColor="#818cf8" />
            <stop offset="1" stopColor="#c084fc" />
          </linearGradient>
          <clipPath id={ids.id("clip")}>
            <path d={GEM} />
          </clipPath>
        </>
      }
    >
      <path d={GEM} fill={ids.url("gem")} filter={ids.url("glow")} />
      <path
        d="M36 50h48M48 34l-4 16 16 34 16-34-4-16M44 50l16-16 16 16"
        fill="none"
        stroke="#fff"
        strokeOpacity=".55"
        strokeWidth="1.2"
      />
      <g clipPath={ids.url("clip")}>
        <rect className="mark-plans-shine" x="54" y="20" width="12" height="80" fill="#fff" fillOpacity=".55" />
      </g>
      <path className="mark-plans-star-1" d="M88 30l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" fill="#fde68a" />
      <path
        className="mark-plans-star-2"
        d="M30 78l1.5 3.5 3.5 1.5-3.5 1.5-1.5 3.5-1.5-3.5-3.5-1.5 3.5-1.5z"
        fill="#c4b5fd"
      />
    </MarkTile>
  );
}
