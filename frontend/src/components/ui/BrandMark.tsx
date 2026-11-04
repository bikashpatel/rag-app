import logo from "../../assets/brand/logo-96.webp";
import logo2x from "../../assets/brand/logo-192.webp";

/**
 * The AskNuvexusDoc logo (the owner's N with its golden orbit, 2026-11-04) beside the name; decorative, the name says
 * it all. `twinkle` (the front door's pages) makes the logo's star glint every few seconds.
 */
export default function BrandMark({ twinkle = false }: { twinkle?: boolean }) {
  return (
    <span className={twinkle ? "ui-brand-mark ui-brand-mark-twinkle" : "ui-brand-mark"} aria-hidden="true">
      <img src={logo} srcSet={`${logo2x} 2x`} width={30} height={30} alt="" />
    </span>
  );
}
