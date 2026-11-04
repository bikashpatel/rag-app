import MarkTile, { useMarkIds, type MarkProps } from "./MarkTile";

/** All set: a green check draws itself with a little burst (a plan started, an email confirmed). */
export default function AllSetMark(props: MarkProps) {
  const ids = useMarkIds();
  return (
    <MarkTile
      {...props}
      kind="all-set"
      ids={ids}
      rim="#6ee7b7"
      glow={3}
      defs={
        <linearGradient id={ids.id("ok")} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#34d399" />
          <stop offset="1" stopColor="#22d3ee" />
        </linearGradient>
      }
    >
      <path
        className="mark-all-set-rays"
        d="M60 22v8M60 90v8M22 60h8M90 60h8M33 33l6 6M81 81l6 6M87 33l-6 6M39 81l-6 6"
        stroke="#6ee7b7"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <circle className="mark-all-set-pop" cx="60" cy="60" r="22" fill={ids.url("ok")} filter={ids.url("glow")} />
      <path
        className="mark-all-set-check"
        d="M50 60l7 7 14-15"
        fill="none"
        stroke="#fff"
        strokeWidth="5"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray="60"
      />
    </MarkTile>
  );
}
