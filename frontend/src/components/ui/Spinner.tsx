/** A ring in the text colour (currentColor), turning; still (but visible) under reduced motion. */
export default function Spinner({ size = 16, label = "Loading" }: { size?: number; label?: string }) {
  return (
    <span
      className="spinner"
      style={{ width: size, height: size, borderWidth: size >= 20 ? 2.25 : undefined }}
      role="status"
      aria-label={label}
    />
  );
}
