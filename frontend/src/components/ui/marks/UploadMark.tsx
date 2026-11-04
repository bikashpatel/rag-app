import MarkTile, { RIM_VIOLET, useMarkIds, type MarkProps } from "./MarkTile";

/** Upload: an arrow keeps rising into a cloud while small sparks twinkle around it. */
export default function UploadMark(props: MarkProps) {
  const ids = useMarkIds();
  return (
    <MarkTile
      {...props}
      kind="upload"
      ids={ids}
      rim={RIM_VIOLET}
      glow={3}
      defs={
        <linearGradient id={ids.id("cloud")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#e0f2fe" />
          <stop offset="1" stopColor="#93c5fd" />
        </linearGradient>
      }
    >
      <path
        d="M38 76h44a14 14 0 0 0 0-28 20 20 0 0 0-38-4 13 13 0 0 0-6 32z"
        fill={ids.url("cloud")}
        filter={ids.url("glow")}
      />
      <path
        className="mark-upload-arrow"
        d="M60 74V52m-9 9 9-9 9 9"
        fill="none"
        stroke="#4f46e5"
        strokeWidth="5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle className="mark-upload-spark-1" cx="34" cy="40" r="2" fill="#67e8f9" />
      <circle className="mark-upload-spark-2" cx="88" cy="36" r="2.4" fill="#c4b5fd" />
      <circle className="mark-upload-spark-3" cx="92" cy="86" r="1.8" fill="#67e8f9" />
    </MarkTile>
  );
}
