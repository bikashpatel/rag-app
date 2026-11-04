import MarkTile, { RIM_SKY, useMarkIds, type MarkProps } from "./MarkTile";

const LABEL = { textAnchor: "middle", fontFamily: "system-ui", fontWeight: 800, fill: "#fff" } as const;

/** Documents: three files fan out while a light scans them, as if read and indexed. */
export default function DocumentsMark(props: MarkProps) {
  const ids = useMarkIds();
  return (
    <MarkTile
      {...props}
      kind="documents"
      ids={ids}
      rim={RIM_SKY}
      glow={3}
      defs={
        <clipPath id={ids.id("clip")}>
          <rect x="6" y="6" width="108" height="108" rx="28" />
        </clipPath>
      }
    >
      <g clipPath={ids.url("clip")}>
        <g className="mark-documents-fan-left">
          <rect x="22" y="36" width="34" height="44" rx="6" fill="#ef4444" />
          <text x="33" y="52" fontSize="8" {...LABEL}>
            PDF
          </text>
        </g>
        <g className="mark-documents-fan-right">
          <rect x="64" y="36" width="34" height="44" rx="6" fill="#22c55e" />
          <text x="88" y="52" fontSize="8" {...LABEL}>
            XLS
          </text>
        </g>
        <rect x="43" y="30" width="34" height="46" rx="6" fill="#3b82f6" />
        <text x="60" y="58" fontSize="13" {...LABEL}>
          W
        </text>
        <rect x="50" y="64" width="20" height="2.5" rx="1.2" fill="#dbeafe" />
        <rect
          className="mark-documents-scan"
          x="58"
          y="24"
          width="3"
          height="62"
          rx="1.5"
          fill="#67e8f9"
          filter={ids.url("glow")}
        />
      </g>
    </MarkTile>
  );
}
