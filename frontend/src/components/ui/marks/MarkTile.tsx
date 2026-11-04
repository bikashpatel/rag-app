import { useId, type ReactNode } from "react";
import "./marks.css";

export type MarkProps = { size?: number; className?: string };

export type MarkIds = { id: (part: string) => string; url: (part: string) => string };

export const RIM_SKY = "#7dd3fc";
export const RIM_VIOLET = "#c4b5fd";

/** Ids for a mark's gradients, filters and clips, unique per instance: useId keeps only what url(#…) accepts. */
export function useMarkIds(): MarkIds {
  const base = `mark${useId().replace(/[^\w-]/g, "")}`;
  return { id: (part) => `${base}-${part}`, url: (part) => `url(#${base}-${part})` };
}

type MarkTileProps = MarkProps & {
  /** Names the mark's class (mark-<kind>), which sets its parts' motion in marks.css. */
  kind: string;
  ids: MarkIds;
  rim: string;
  /** The blur of the shared glow filter, `ids.url("glow")`, for marks that use it. */
  glow?: number;
  defs?: ReactNode;
  children: ReactNode;
};

/**
 * The tile every mark sits on, in the owner's neon robot style (2026-11-04): a navy-to-violet rounded square with a
 * neon rim that floats gently. Decorative: hidden from assistive technology, the text beside it says it all.
 */
export default function MarkTile({ kind, ids, rim, glow, defs, size = 72, className, children }: MarkTileProps) {
  return (
    <svg
      className={className ? `mark mark-${kind} ${className}` : `mark mark-${kind}`}
      width={size}
      height={size}
      viewBox="0 0 120 120"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={ids.id("tile")} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#0f1d55" />
          <stop offset="1" stopColor="#2a1170" />
        </linearGradient>
        {glow ? (
          <filter id={ids.id("glow")} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation={glow} result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        ) : null}
        {defs}
      </defs>
      <g className="mark-float">
        <rect x="6" y="6" width="108" height="108" rx="28" fill={ids.url("tile")} />
        <rect
          x="6.5"
          y="6.5"
          width="107"
          height="107"
          rx="27.5"
          fill="none"
          stroke={rim}
          strokeOpacity=".45"
          strokeWidth="1"
        />
        {children}
      </g>
    </svg>
  );
}
