import MarkTile, { RIM_SKY, useMarkIds, type MarkProps } from "./MarkTile";

/** Settings: two gears turning against each other, steady and unhurried. */
export default function SettingsMark(props: MarkProps) {
  const ids = useMarkIds();
  const gear = `#${ids.id("gear")}`;
  return (
    <MarkTile
      {...props}
      kind="settings"
      ids={ids}
      rim={RIM_SKY}
      defs={
        <>
          <linearGradient id={ids.id("metal")} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#93c5fd" />
            <stop offset="1" stopColor="#a78bfa" />
          </linearGradient>
          {/* The gear is centred on (0, 4): the hubs sit there, so each gear turns about its own axle. */}
          <path
            id={ids.id("gear")}
            d="M-3-16h6l1 5 4 2 4-3 4 4-3 4 2 4 5 1v6l-5 1-2 4 3 4-4 4-4-3-4 2-1 5h-6l-1-5-4-2-4 3-4-4 3-4-2-4-5-1v-6l5-1 2-4-3-4 4-4 4 3 4-2z"
          />
        </>
      }
    >
      <g transform="translate(50 56)">
        <g className="mark-settings-big">
          <use href={gear} fill={ids.url("metal")} transform="scale(1.25)" />
          <circle cy="5" r="7" fill="#1e2a6b" />
        </g>
      </g>
      <g transform="translate(77 79)">
        <g className="mark-settings-small">
          <use href={gear} fill="#67e8f9" transform="scale(.8)" />
          <circle cy="3.2" r="4.5" fill="#1e2a6b" />
        </g>
      </g>
    </MarkTile>
  );
}
